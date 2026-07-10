"""Dataset ingestion + access for the ad-hoc query layer.

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

from app.core.config import get_settings
from app.models.dataset import Dataset
from app.services import storage


def qi(name: str) -> str:
    """Quote a SQL identifier (columns are whitelisted against headers upstream)."""
    return '"' + str(name).replace('"', '""') + '"'


def qs(value: str) -> str:
    """Quote a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def open_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def relation_for(path: Path) -> str:
    """DuckDB table-function for a stored dataset file. CSV is read ALL_VARCHAR so
    numeric coercion in the query layer applies uniformly."""
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
        # nosec B608 - rel is the DuckDB relation string built by relation_for()
        # above, which quote-escapes the file path itself; no column identifiers here.
        desc = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()  # nosec B608
        columns = [{"name": str(r[0]), "dtype": str(r[1])} for r in desc]
        row_count = int(con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0])  # nosec B608
    finally:
        con.close()
    return columns, row_count


def json_safe(value: Any) -> Any:
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


async def list_datasets(db: AsyncSession, org_id: uuid.UUID) -> list[Dataset]:
    result = await db.execute(
        select(Dataset).where(Dataset.org_id == org_id).order_by(Dataset.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_dataset(db: AsyncSession, org_id: uuid.UUID, dataset_id: str) -> Dataset:
    """Parse a client-supplied dataset id string and load the tenant-scoped row.
    Shared by every dataset route (400 on a malformed id, 404 if not found)."""
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


async def delete_dataset(db: AsyncSession, dataset: Dataset) -> None:
    storage.delete_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext)
    await db.delete(dataset)
    await db.flush()


def validate_columns(dataset: Dataset, cols: Iterable[str | None]) -> None:
    """Reject any mapped column that is not a real header of this dataset. Column
    names are interpolated into SQL (identifier-quoted by qi), so this
    allow-list is the security boundary — only known headers may pass."""
    known = {c["name"] for c in dataset.columns}
    for col in cols:
        if col and col not in known:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Column not found in dataset: '{col}'")


def dataframe_for(dataset: Dataset):
    """Full dataset as a pandas DataFrame, via the same DuckDB relation every
    other reader uses (CSV columns come through as strings — callers coerce
    the specific columns they need, same as the rest of this module)."""
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        return con.execute(f"SELECT * FROM {rel}").df()  # nosec B608 - rel is the quote-escaped relation string, no identifiers
    finally:
        con.close()


def preview_rows(dataset: Dataset, limit: int = 50) -> list[dict]:
    rel = dataset_relation(dataset)
    con = open_con()
    try:
        cur = con.execute(f"SELECT * FROM {rel} LIMIT {int(limit)}")  # nosec B608 - rel is the quote-escaped relation string, limit is int()-cast
        names = [d[0] for d in (cur.description or [])]
        return [{n: json_safe(v) for n, v in zip(names, row)} for row in cur.fetchall()]
    finally:
        con.close()


def _blank_expr(col: str) -> str:
    """SQL for a column's value with blanks (incl. whitespace-only) normalized to NULL."""
    return f"NULLIF(TRIM(CAST({qi(col)} AS VARCHAR)), '')"


# ─── Health ───────────────────────────────────────────────────────────────────
# Per-column profiler: null/distinct counts, top values, numeric stats, and IQR
# outlier fences (needs >=8 numeric values). Recomputed live via DuckDB SQL
# instead of loading rows into Python — cheap at dataset scale.

def compute_health(dataset: Dataset) -> dict:
    rel = dataset_relation(dataset)
    row_count = dataset.row_count
    names = [c["name"] for c in dataset.columns]
    con = open_con()
    try:
        dup_count = 0
        if row_count and names:
            key = "concat_ws(chr(31), " + ", ".join(f"COALESCE(CAST({qi(n)} AS VARCHAR), '')" for n in names) + ")"
            # nosec B608 - key/rel below are built from qi()-quoted dataset column
            # names (names comes straight from the sniffed schema, not free text).
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
            # _blank_expr); n below comes from `names`, the dataset's own sniffed
            # schema, not user-supplied free text.
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
