from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.dataset import Dataset
from app.services import dataset_query as query_service
from app.services import datasets as ds_service
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetColumn(BaseModel):
    name: str
    dtype: str


class DatasetResponse(BaseModel):
    id: str
    name: str
    original_filename: str
    fmt: str
    columns: list[DatasetColumn]
    row_count: int
    size_bytes: int
    created_at: str


class DatasetDetailResponse(DatasetResponse):
    preview: list[dict]


class HealthColumn(BaseModel):
    name: str
    inferred_dtype: str
    null_count: int
    null_pct: float
    distinct_count: int
    top_values: list[dict]
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None
    numeric_count: int | None = None
    outlier_count: int | None = None
    outlier_pct: float | None = None


class DatasetHealthResponse(BaseModel):
    row_count: int
    column_count: int
    duplicate_rows: int
    duplicate_rows_pct: float
    total_null_cells: int
    total_cells: int
    null_cells_pct: float
    columns: list[HealthColumn]
    warnings: list[str]


class DatasetQueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict]


def _to_response(ds: Dataset) -> DatasetResponse:
    return DatasetResponse(
        id=str(ds.id),
        name=ds.name,
        original_filename=ds.original_filename,
        fmt=ds.fmt,
        columns=[DatasetColumn(**c) for c in ds.columns],
        row_count=ds.row_count,
        size_bytes=ds.size_bytes,
        created_at=ds.created_at.isoformat(),
    )


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    dataset = await ds_service.create_dataset(
        db,
        org_id=current.org_id,
        created_by=current.id,
        name=name,
        filename=file.filename or "upload",
        data=data,
    )
    await log_event(
        db,
        action="dataset.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(dataset.id),
        metadata={"name": dataset.name, "rows": dataset.row_count},
    )
    await db.commit()
    await db.refresh(dataset)
    return _to_response(dataset)


@router.get("", response_model=list[DatasetResponse])
async def list_datasets(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await ds_service.list_datasets(db, current.org_id)
    return [_to_response(d) for d in rows]


@router.get("/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset(
    dataset_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    base = _to_response(dataset)
    return DatasetDetailResponse(**base.model_dump(), preview=ds_service.preview_rows(dataset, limit=50))


@router.get("/{dataset_id}/health", response_model=DatasetHealthResponse)
async def dataset_health(
    dataset_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    return ds_service.compute_health(dataset)


@router.post("/{dataset_id}/query", response_model=DatasetQueryResponse)
async def query_dataset(
    dataset_id: uuid.UUID,
    req: query_service.DatasetQueryRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    return await query_service.run_query(db, dataset, current.org_id, req)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    await ds_service.delete_dataset(db, dataset)
    await log_event(
        db,
        action="dataset.deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(dataset_id),
    )
    await db.commit()
