"""Dataset ingestion + access for the ad-hoc query layer and the analytics engines.

Uploads land as DuckDB-native files (xlsx/xls are converted to CSV on ingest);
schema + row count are sniffed with DuckDB. Every read is tenant-scoped, and
``validate_columns`` gates any column name before it reaches a SQL builder.
"""
from __future__ import annotations

import io
import re  # noqa: F401
import uuid
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path
from typing import Any, Iterable  # noqa: F401

import duckdb  # noqa: F401
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import qi, qs  # noqa: F401
from app.api.deps import get_or_404
from app.core.config import get_settings
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import automl_storage, storage
from app.services.datasets_ops import (  # noqa: F401
    _blank_expr, _detect_timestamp_col, _materialize, _sniff, clean_dataset, compute_health, dataset_relation,
    json_safe, missing_columns, open_con, pivot_dataset, relation_for, select_columns_dataset, validate_columns,
)


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


async def overwrite_dataset_file(
    db: AsyncSession, dataset: Dataset, *, data: bytes, background_tasks: "BackgroundTasks | None" = None
) -> Dataset:
    """Rewrite an existing, already-created dataset's file in place and
    re-sniff its schema/row count. Used by live-capture features (the
    downtime-tracking board, shift-forecast source) whose Dataset keeps
    growing/refreshing instead of being created once - see
    storage.replace_dataset_file for the atomicity note.

    When background_tasks is provided, also schedules a check of whether
    any AutoML run watching this dataset (AutoMLRun.auto_retrain=True)
    should be retrained for drift - see app.services.automl_retrain. The
    import below is local, not at module level, specifically to avoid a
    circular import: automl_retrain imports dataframe_for from this same
    module at its own module level.
    """
    storage.replace_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext, data)
    columns, row_count = _sniff(storage.dataset_path(dataset.org_id, dataset.id, dataset.storage_ext))
    dataset.columns = columns
    dataset.row_count = row_count
    dataset.size_bytes = len(data)
    await db.flush()
    if background_tasks is not None:
        from app.services import automl_retrain

        background_tasks.add_task(
            automl_retrain.maybe_retrain_for_dataset, str(dataset.org_id), str(dataset.id)
        )
    return dataset


async def list_datasets(db: AsyncSession, org_id: uuid.UUID) -> list[Dataset]:
    result = await db.execute(
        select(Dataset).where(Dataset.org_id == org_id).order_by(Dataset.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_dataset(db: AsyncSession, org_id: uuid.UUID, dataset_id: str) -> Dataset:
    """Parse a client-supplied dataset id string and load the tenant-scoped row.
    Shared by every dataset/analytics route (400 on a malformed id, 404 if not found)."""
    try:
        did = uuid.UUID(dataset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid dataset_id") from None
    return await get_dataset_or_404(db, org_id, did)


async def get_dataset_or_404(db: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    return await get_or_404(db, Dataset, org_id, dataset_id, "Dataset not found")


async def delete_dataset(db: AsyncSession, dataset: Dataset) -> None:
    runs = (
        await db.execute(select(AutoMLRun.id).where(AutoMLRun.dataset_id == dataset.id))
    ).scalars().all()
    for run_id in runs:
        automl_storage.delete_run_dir(dataset.org_id, run_id)
    storage.delete_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext)
    await db.delete(dataset)
    await db.flush()


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
