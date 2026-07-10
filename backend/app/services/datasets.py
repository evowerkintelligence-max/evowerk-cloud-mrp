"""Dataset ingestion + access for the analytics layer.

Uploads land as DuckDB-native files (xlsx/xls are converted to CSV on ingest);
schema + row count are sniffed with DuckDB. Every read is tenant-scoped, and
``validate_columns`` gates any column name before it reaches a SQL builder.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import qi, qs
from app.core.config import get_settings
from app.models.dataset import Dataset
from app.services import storage


def open_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def relation_for(path: Path) -> str:
    """DuckDB table-function for a stored dataset file. CSV is read ALL_VARCHAR so
    the engines' numeric coercion applies uniformly (matches oee_calc.agg_relation)."""
    ext = path.suffix.lower()
    p = str(path).replace("'", "''")
    if ext == ".parquet":
        return f"read_parquet('{p}')"
    if ext in (".json", ".jsonl", ".ndjson"):
        return f"read_json_auto('{p}')"
    if ext == ".tsv":
        return f"read_csv_auto('{p}', ALL_VARCHAR=1, header=true, delim='\t')"
    return f"read_csv_auto('{p}', ALL_VARCHAR=1, header=true)"


def dataset_relation(dataset: Dataset) -> str:
    """Resolve a dataset to its DuckDB relation string, verifying the file exists."""
    path = storage.dataset_path(dataset.org_id, dataset.id, dataset.storage_ext)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset file is missing on disk")
    return relation_for(path)


def _sniff(path: Path) -> tuple[list[dict], int]:
    rel = relation_for(path)
    con = open_con()
    try:
        # nosec B608 - rel is the DuckDB relation string from relation_for(),
        # which quote-escapes the file path; no column identifiers here.
        desc = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()  # nosec B608
        columns = [{"name": str(r[0]), "dtype": str(r[1])} for r in desc]
        row_count = int(con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0])  # nosec B608
    finally:
        con.close()
    return columns, row_count


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def create_dataset(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str | None,
    filename: str,
    data: bytes,
) -> Dataset:
    settings = get_settings()
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in settings.allowed_dataset_exts:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type '.{ext}'. Allowed: {', '.join(settings.allowed_dataset_exts)}",
        )
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_upload_mb} MB limit",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")

    dataset_id = uuid.uuid4()

    # Normalize to a DuckDB-native stored form. Excel needs pandas; everything
    # else DuckDB reads directly, so the stored extension stays authoritative.
    if ext in ("xlsx", "xls"):
        import pandas as pd

        try:
            df = pd.read_excel(io.BytesIO(data))
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Could not read Excel file: {exc}") from exc
        data = df.to_csv(index=False).encode("utf-8")
        storage_ext = "csv"
    elif ext in ("jsonl", "ndjson"):
        storage_ext = ext
    else:
        storage_ext = ext

    fmt = {"tsv": "tsv", "jsonl": "json", "ndjson": "json"}.get(storage_ext, storage_ext)

    storage.save_dataset_file(org_id, dataset_id, storage_ext, data)
    try:
        columns, row_count = _sniff(storage.dataset_path(org_id, dataset_id, storage_ext))
    except Exception as exc:
        storage.delete_dataset_file(org_id, dataset_id, storage_ext)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Could not parse dataset: {exc}") from exc

    dataset = Dataset(
        id=dataset_id,
        org_id=org_id,
        name=(name or filename),
        original_filename=filename,
        fmt=fmt,
        storage_ext=storage_ext,
        columns=columns,
        row_count=row_count,
        size_bytes=len(data),
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dataset)
    await db.flush()
    return dataset


async def resolve_dataset(db: AsyncSession, org_id: uuid.UUID, dataset_id: str) -> Dataset:
    """Parse a client-supplied dataset id string and load the tenant-scoped row.
    Shared by every analytics route (400 on a malformed id, 404 if not found)."""
    try:
        did = uuid.UUID(dataset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid dataset_id") from None
    return await get_dataset_or_404(db, org_id, did)


async def get_dataset_or_404(db: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id))
    ).scalar_one_or_none()
    if dataset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return dataset


def validate_columns(dataset: Dataset, cols: Iterable[str | None]) -> None:
    """Reject any mapped column that is not a real header of this dataset. Column
    names are interpolated into SQL (identifier-quoted by oee_calc.qi), so this
    allow-list is the security boundary — only known headers may pass."""
    known = {c["name"] for c in dataset.columns}
    for col in cols:
        if col and col not in known:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Column not found in dataset: '{col}'")


def preview_rows(dataset: Dataset, limit: int = 50) -> list[dict]:
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        cur = con.execute(f"SELECT * FROM {rel} LIMIT {int(limit)}")  # nosec B608 - rel is the quote-escaped relation string, limit is int()-cast
        names = [d[0] for d in (cur.description or [])]
        return [{n: _json_safe(v) for n, v in zip(names, row)} for row in cur.fetchall()]
    finally:
        con.close()


def _blank_expr(col: str) -> str:
    """SQL for a column's value with blanks (incl. whitespace-only) normalized to NULL."""
    return f"NULLIF(TRIM(CAST({qi(col)} AS VARCHAR)), '')"


# ─── Health ───────────────────────────────────────────────────────────────────
# Ported from the desktop app's per-column profiler (routes/datasets.py
# _compute_health): null/distinct counts, top values, numeric stats, and IQR
# outlier fences (needs >=8 numeric values). Recomputed live via DuckDB SQL
# instead of loading rows into Python — cheap at analytics-dataset scale.

def compute_health(dataset: Dataset) -> dict:
    rel = dataset_relation(dataset)
    row_count = dataset.row_count
    names = [c["name"] for c in dataset.columns]
    con = open_con()
    try:
        dup_count = 0
        if row_count and names:
            key = "concat_ws(chr(31), " + ", ".join(f"COALESCE(CAST({qi(n)} AS VARCHAR), '')" for n in names) + ")"
            # nosec B608 - key/rel are built from qi()-quoted dataset column
            # names (names comes from the sniffed schema, not free text).
            dup_count = int(
                con.execute(
                    f"SELECT COUNT(*) FROM ("  # nosec B608
                    f"SELECT ROW_NUMBER() OVER (PARTITION BY {key}) AS rn FROM {rel}"
                    f") WHERE rn > 1"
                ).fetchone()[0]
            )

        columns: list[dict] = []
        total_null = 0
        outlier_cols: list[tuple[str, float]] = []

        for n in names:
            blank = _blank_expr(n)
            # nosec B608 - blank/rel are built from qi()-quoted column names (see
            # _blank_expr); n comes from `names`, the dataset's own sniffed schema.
            null_count, distinct_count = con.execute(
                f"SELECT COUNT(*) FILTER (WHERE {blank} IS NULL), COUNT(DISTINCT {blank}) FROM {rel}"  # nosec B608
            ).fetchone()
            total_null += null_count

            top_rows = con.execute(
                f"SELECT {blank} AS v, COUNT(*) AS n FROM {rel} WHERE {blank} IS NOT NULL "  # nosec B608
                f"GROUP BY v ORDER BY n DESC LIMIT 5"
            ).fetchall()
            top_values = [{"value": v, "count": int(c)} for v, c in top_rows]

            num_min, num_max, num_mean, num_std, num_count = con.execute(
                f"SELECT MIN(v), MAX(v), AVG(v), STDDEV_POP(v), COUNT(v) FROM "  # nosec B608
                f"(SELECT TRY_CAST({blank} AS DOUBLE) AS v FROM {rel}) WHERE v IS NOT NULL"
            ).fetchone()

            inferred = "text"
            non_null = row_count - null_count
            if num_count and non_null and num_count >= non_null * 0.9:
                inferred = "numeric"
            elif non_null:
                date_count = con.execute(
                    f"SELECT COUNT(*) FROM (SELECT TRY_CAST({blank} AS TIMESTAMP) AS v FROM {rel}) WHERE v IS NOT NULL"  # nosec B608
                ).fetchone()[0]
                if date_count >= non_null * 0.9:
                    inferred = "datetime"

            outlier_count = outlier_pct = None
            if num_count and num_count >= 8:
                q1, q3 = con.execute(
                    f"SELECT quantile_cont(v, 0.25), quantile_cont(v, 0.75) FROM "  # nosec B608
                    f"(SELECT TRY_CAST({blank} AS DOUBLE) AS v FROM {rel}) WHERE v IS NOT NULL"
                ).fetchone()
                if q1 is not None and q3 is not None and (q3 - q1) > 0:
                    iqr = q3 - q1
                    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                    outlier_count = int(
                        con.execute(
                            f"SELECT COUNT(*) FROM (SELECT TRY_CAST({blank} AS DOUBLE) AS v FROM {rel}) "  # nosec B608
                            f"WHERE v IS NOT NULL AND (v < {low} OR v > {high})"
                        ).fetchone()[0]
                    )
                    outlier_pct = round(outlier_count / num_count * 100, 2)
                    outlier_cols.append((n, outlier_pct))

            columns.append(
                {
                    "name": n,
                    "inferred_dtype": inferred,
                    "null_count": int(null_count),
                    "null_pct": round(null_count / row_count * 100, 2) if row_count else 0.0,
                    "distinct_count": int(distinct_count),
                    "top_values": top_values,
                    "min": num_min,
                    "max": num_max,
                    "mean": round(num_mean, 4) if num_mean is not None else None,
                    "std": round(num_std, 4) if num_std is not None else None,
                    "numeric_count": int(num_count) if num_count else None,
                    "outlier_count": outlier_count,
                    "outlier_pct": outlier_pct,
                }
            )
    finally:
        con.close()

    total_cells = row_count * len(names)
    warnings: list[str] = []
    null_pct = round(total_null / total_cells * 100, 2) if total_cells else 0.0
    dup_pct = round(dup_count / row_count * 100, 2) if row_count else 0.0
    if null_pct > 25:
        warnings.append("High missingness detected (null cells > 25%)")
    if dup_pct > 5:
        warnings.append("Notable duplicates detected (duplicate rows > 5%)")
    if outlier_cols:
        outlier_cols.sort(key=lambda x: x[1], reverse=True)
        warnings.append(f"Outliers detected in columns: {', '.join(n for n, _ in outlier_cols[:3])}")

    return {
        "row_count": row_count,
        "column_count": len(names),
        "duplicate_rows": dup_count,
        "duplicate_rows_pct": dup_pct,
        "total_null_cells": total_null,
        "total_cells": total_cells,
        "null_cells_pct": null_pct,
        "columns": columns,
        "warnings": warnings,
    }


# ─── Clean & Transform ────────────────────────────────────────────────────────
# Both create a new dataset rather than mutating in place (datasets are
# read-only after upload, see Dataset's docstring) — mirrors the desktop app's
# "clean/transform always produces a new version" behavior.

def _materialize(db: AsyncSession, con: duckdb.DuckDBPyConnection, select_sql: str, *, org_id, created_by, name: str) -> Dataset:
    new_id = uuid.uuid4()
    out_path = storage.dataset_path(org_id, new_id, "csv")
    p = str(out_path).replace("'", "''")
    con.execute(f"COPY ({select_sql}) TO '{p}' (HEADER, FORMAT CSV)")
    columns, row_count = _sniff(out_path)
    dataset = Dataset(
        id=new_id,
        org_id=org_id,
        name=name,
        original_filename=f"{name}.csv",
        fmt="csv",
        storage_ext="csv",
        columns=columns,
        row_count=row_count,
        size_bytes=out_path.stat().st_size,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dataset)
    return dataset


async def clean_dataset(
    db: AsyncSession, dataset: Dataset, *, org_id, created_by, req: Any, output_name: str
) -> tuple[Dataset, list[str]]:
    headers = [c["name"] for c in dataset.columns]
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        ops: list[str] = []
        fill_map: dict[str, str] = {}
        if req.fill_missing:
            strategy = (req.fill_strategy or "custom").lower()
            for c in headers:
                blank = _blank_expr(c)
                if strategy in ("mean", "median"):
                    fn = "AVG" if strategy == "mean" else "MEDIAN"
                    # nosec B608 - c/blank are qi()-quoted headers (from dataset.columns)
                    val = con.execute(f"SELECT {fn}(TRY_CAST({blank} AS DOUBLE)) FROM {rel}").fetchone()[0]  # nosec B608
                    fill_map[c] = qs(str(val)) if val is not None else "''"
                elif strategy == "mode":
                    row = con.execute(
                        f"SELECT {blank} AS v FROM {rel} WHERE {blank} IS NOT NULL "  # nosec B608
                        f"GROUP BY v ORDER BY COUNT(*) DESC LIMIT 1"
                    ).fetchone()
                    fill_map[c] = qs(row[0]) if row else "''"
                else:
                    strategy = "custom"
                    fill_map[c] = qs(req.fill_value or "")
            ops.append(f"Fill missing with {strategy}")

        if req.trim_strings:
            ops.append("Trim strings")

        def step1_expr(c: str) -> str:
            ident = qi(c)
            base = f"TRIM(CAST({ident} AS VARCHAR))" if req.trim_strings else f"CAST({ident} AS VARCHAR)"
            if req.fill_missing:
                return f"CASE WHEN {_blank_expr(c)} IS NULL THEN {fill_map[c]} ELSE {base} END AS {ident}"
            return f"{base} AS {ident}"

        # nosec B608 - step1_expr/headers are qi()-quoted dataset column names
        # (from dataset.columns, the sniffed schema), not free-text input.
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW _clean1 AS SELECT {', '.join(step1_expr(c) for c in headers)} FROM {rel}"  # nosec B608
        )
        cur = "_clean1"

        if req.remove_outliers:
            factor = req.outlier_iqr_factor or 1.5
            target = [c for c in (req.outlier_columns or headers) if c in headers]
            fences: dict[str, tuple[float, float]] = {}
            for c in target:
                q1, q3, n = con.execute(
                    f"SELECT quantile_cont(v, 0.25), quantile_cont(v, 0.75), COUNT(v) FROM "  # nosec B608
                    f"(SELECT TRY_CAST({qi(c)} AS DOUBLE) AS v FROM {cur}) WHERE v IS NOT NULL"
                ).fetchone()
                if n and n >= 8 and q1 is not None and q3 is not None and (q3 - q1) > 0:
                    iqr = q3 - q1
                    fences[c] = (q1 - factor * iqr, q3 + factor * iqr)

            if fences:
                action = (req.outlier_strategy or "remove").lower()
                if action == "cap":
                    exprs = []
                    for c in headers:
                        ident = qi(c)
                        if c in fences:
                            low, high = fences[c]
                            exprs.append(
                                f"CASE WHEN TRY_CAST({ident} AS DOUBLE) < {low} THEN '{low}' "
                                f"WHEN TRY_CAST({ident} AS DOUBLE) > {high} THEN '{high}' "
                                f"ELSE {ident} END AS {ident}"
                            )
                        else:
                            exprs.append(ident)
                    con.execute(f"CREATE OR REPLACE TEMP VIEW _clean2 AS SELECT {', '.join(exprs)} FROM {cur}")  # nosec B608
                    ops.append(f"Cap outliers (IQR {factor})")
                else:
                    where = " AND ".join(
                        f"(TRY_CAST({qi(c)} AS DOUBLE) IS NULL OR TRY_CAST({qi(c)} AS DOUBLE) BETWEEN {low} AND {high})"
                        for c, (low, high) in fences.items()
                    )
                    con.execute(f"CREATE OR REPLACE TEMP VIEW _clean2 AS SELECT * FROM {cur} WHERE {where}")  # nosec B608
                    ops.append(f"Remove outliers (IQR {factor})")
                cur = "_clean2"

        if req.standardize_numeric:
            stats: dict[str, tuple[float, float]] = {}
            for c in headers:
                mean, std, n = con.execute(
                    f"SELECT AVG(v), STDDEV_POP(v), COUNT(v) FROM "  # nosec B608
                    f"(SELECT TRY_CAST({qi(c)} AS DOUBLE) AS v FROM {cur}) WHERE v IS NOT NULL"
                ).fetchone()
                if n and n >= 2 and std:
                    stats[c] = (mean, std)
            if stats:
                exprs = [
                    (
                        f"CAST((TRY_CAST({qi(c)} AS DOUBLE) - {stats[c][0]}) / {stats[c][1]} AS VARCHAR) AS {qi(c)}"
                        if c in stats
                        else qi(c)
                    )
                    for c in headers
                ]
                con.execute(f"CREATE OR REPLACE TEMP VIEW _clean3 AS SELECT {', '.join(exprs)} FROM {cur}")  # nosec B608
                cur = "_clean3"
                ops.append("Standardize numeric columns (z-score)")

        if req.drop_duplicates:
            ops.append("Drop duplicates")
        select_sql = f"SELECT {'DISTINCT ' if req.drop_duplicates else ''}* FROM {cur}"  # nosec B608 - cur is an internal temp-view name, not user input

        dataset_out = _materialize(db, con, select_sql, org_id=org_id, created_by=created_by, name=output_name)
    finally:
        con.close()
    await db.flush()
    return dataset_out, ops


async def select_columns_dataset(
    db: AsyncSession, dataset: Dataset, *, org_id, created_by, columns: list[str], output_name: str
) -> tuple[Dataset, list[str]]:
    headers = [c["name"] for c in dataset.columns]
    keep = [c for c in columns if c in headers]
    if not keep:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "None of the requested columns exist in this dataset")
    dropped = [h for h in headers if h not in keep]
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        select_sql = f"SELECT {', '.join(qi(c) for c in keep)} FROM {rel}"  # nosec B608 - keep is filtered to `c in headers`, the sniffed schema
        dataset_out = _materialize(db, con, select_sql, org_id=org_id, created_by=created_by, name=output_name)
    finally:
        con.close()
    await db.flush()
    return dataset_out, dropped


def _detect_timestamp_col(headers: list[str]) -> str | None:
    for pattern in ("timestamp", "time", "datetime", "date", "ts", "t"):
        for h in headers:
            if h.lower() == pattern:
                return h
    for h in headers:
        if any(p in h.lower() for p in ("time", "date", "ts")):
            return h
    return headers[0] if headers else None


async def pivot_dataset(
    db: AsyncSession, dataset: Dataset, *, org_id, created_by, req: Any, output_name: str
) -> Dataset:
    headers = [c["name"] for c in dataset.columns]
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        direction = (req.direction or "wide_to_tall").lower()
        if direction == "wide_to_tall":
            ts_col = req.timestamp_col if req.timestamp_col in headers else _detect_timestamp_col(headers)
            if not ts_col:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not detect a timestamp column — map one explicitly")
            requested = req.tag_cols or [h for h in headers if h != ts_col]
            pivot_cols = [c for c in requested if c in headers and c != ts_col]
            if not pivot_cols:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No tag columns to pivot")
            passthrough = [h for h in headers if h != ts_col and h not in pivot_cols]
            out_ts = req.output_timestamp_col or "timestamp"
            out_tag = req.output_tag_col or "tag"
            out_val = req.output_value_col or "value"
            keep = [ts_col] + passthrough + pivot_cols
            inner = ", ".join(
                f"CAST({qi(c)} AS VARCHAR) AS {qi(c)}" if c in pivot_cols else qi(c) for c in keep
            )
            con.execute(f"CREATE OR REPLACE TEMP VIEW _pivot_src AS SELECT {inner} FROM {rel}")  # nosec B608 - inner is built from qi()-quoted, `in headers`-checked columns
            on_list = ", ".join(qi(c) for c in pivot_cols)
            sel = (
                [f"{qi(ts_col)} AS {qi(out_ts)}"]
                + [qi(pc) for pc in passthrough]
                + [f"tag AS {qi(out_tag)}", f"value AS {qi(out_val)}"]
            )
            select_sql = (
                f"SELECT {', '.join(sel)} FROM (UNPIVOT _pivot_src ON {on_list} INTO NAME tag VALUE value) "  # nosec B608
                f"WHERE value IS NOT NULL AND trim(CAST(value AS VARCHAR)) <> ''"
            )
        else:
            ts_col = req.timestamp_col if req.timestamp_col in headers else _detect_timestamp_col(headers)
            tag_col = req.tag_col or "tag"
            value_col = req.value_col or "value"
            if not ts_col:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not detect a timestamp column — map one explicitly")
            if tag_col not in headers:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Tag column '{tag_col}' not found")
            if value_col not in headers:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Value column '{value_col}' not found")
            passthrough = [h for h in headers if h not in (ts_col, tag_col, value_col)]
            group_cols = ", ".join(qi(c) for c in ([ts_col] + passthrough))
            select_sql = (
                f"SELECT * FROM (PIVOT {rel} ON {qi(tag_col)} USING first({qi(value_col)}) "  # nosec B608 - tag_col/value_col are checked against headers above
                f"GROUP BY {group_cols})"
            )
        dataset_out = _materialize(db, con, select_sql, org_id=org_id, created_by=created_by, name=output_name)
    finally:
        con.close()
    await db.flush()
    return dataset_out
