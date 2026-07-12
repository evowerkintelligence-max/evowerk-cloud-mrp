from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.middleware.audit import log_event
from app.middleware.rate_limit import rate_limit
from app.models.dataset import Dataset
from app.services import dataset_query as query_service
from app.services import datasets as ds_service
from app.services import query_history as history_service
from app.services import sql_query as sql_query_service

router = APIRouter(prefix="/datasets", tags=["datasets"])

_settings = get_settings()
_sql_rate_limit = rate_limit("dataset_sql_query", _settings.rate_limit_sql_query_per_5min, 300)


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


class SqlQueryRequest(BaseModel):
    sql: str
    limit: int = 500

    @field_validator("sql")
    @classmethod
    def _require_sql(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql must not be empty")
        return v

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v: int) -> int:
        return max(1, min(v, 5000))


class SqlQueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict]


class CleanRequest(BaseModel):
    trim_strings: bool = True
    drop_duplicates: bool = True
    fill_missing: bool = False
    fill_strategy: str | None = None  # custom | mean | median | mode
    fill_value: str | None = None
    remove_outliers: bool = False
    outlier_iqr_factor: float = 1.5
    outlier_columns: list[str] | None = None
    outlier_strategy: str = "remove"  # remove | cap
    standardize_numeric: bool = False
    output_name: str | None = None


class CleanResponse(BaseModel):
    dataset: DatasetResponse
    ops: list[str]


class SelectColumnsRequest(BaseModel):
    columns: list[str]
    output_name: str | None = None


class SelectColumnsResponse(BaseModel):
    dataset: DatasetResponse
    dropped_columns: list[str]


class PivotRequest(BaseModel):
    direction: str = "wide_to_tall"  # wide_to_tall | tall_to_wide
    timestamp_col: str | None = None
    tag_cols: list[str] | None = None
    output_timestamp_col: str = "timestamp"
    output_tag_col: str = "tag"
    output_value_col: str = "value"
    tag_col: str | None = "tag"
    value_col: str | None = "value"
    output_name: str | None = None


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


class QueryHistoryEntryResponse(BaseModel):
    id: str
    action: str
    dataset_id: str | None
    query: dict | None
    sql: str | None
    created_at: str


def _history_to_response(e: history_service.QueryHistoryEntry) -> QueryHistoryEntryResponse:
    return QueryHistoryEntryResponse(
        id=str(e.id), action=e.action, dataset_id=str(e.dataset_id) if e.dataset_id else None,
        query=e.query, sql=e.sql, created_at=e.created_at.isoformat(),
    )


# Registered ahead of GET /{dataset_id} below - that catch-all would otherwise
# shadow the literal /query-history path (Starlette matches routes in add
# order; see the same pattern in app/api/routes/reports.py).
@router.get("/query-history", response_model=list[QueryHistoryEntryResponse])
async def list_query_history(
    dataset_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entries = await history_service.list_query_history(db, current.org_id, dataset_id=dataset_id, limit=limit)
    return [_history_to_response(e) for e in entries]


@router.delete("/query-history/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_query_history_entry(
    entry_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        await history_service.delete_query_history_entry(db, current.org_id, entry_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/query-history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_query_history(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await history_service.clear_query_history(db, current.org_id)


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
    result = await query_service.run_query(db, dataset, current.org_id, req)
    await log_event(
        db,
        action="dataset.query",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(dataset.id),
        metadata={"query": req.model_dump(mode="json")},
    )
    await db.commit()
    return result


@router.post("/{dataset_id}/query/explain")
async def explain_dataset_query(
    dataset_id: uuid.UUID,
    req: query_service.DatasetQueryRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """The compiled SQL for a query, without running it - lets a query-builder
    UI show what will actually execute before spending a real query."""
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    return {"sql": query_service.explain_query(dataset, req)}


@router.post("/{dataset_id}/sql", response_model=SqlQueryResponse, dependencies=[Depends(_sql_rate_limit)])
async def sql_query_dataset(
    dataset_id: uuid.UUID,
    req: SqlQueryRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    result = await sql_query_service.run_sql(
        dataset, req.sql, timeout_seconds=_settings.sql_query_timeout_seconds, row_limit=req.limit
    )
    await log_event(
        db,
        action="dataset.sql_query",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(dataset.id),
        metadata={"sql": req.sql[:2000]},
    )
    await db.commit()
    return result


@router.post("/{dataset_id}/clean", response_model=CleanResponse, status_code=status.HTTP_201_CREATED)
async def clean_dataset(
    dataset_id: uuid.UUID,
    req: CleanRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    output_name = req.output_name or f"{dataset.name} (cleaned)"
    new_ds, ops = await ds_service.clean_dataset(
        db, dataset, org_id=current.org_id, created_by=current.id, req=req, output_name=output_name
    )
    await log_event(
        db,
        action="dataset.cleaned",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(new_ds.id),
        metadata={"source": str(dataset.id), "ops": ops},
    )
    await db.commit()
    await db.refresh(new_ds)
    return CleanResponse(dataset=_to_response(new_ds), ops=ops)


@router.post(
    "/{dataset_id}/transform/select-columns",
    response_model=SelectColumnsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def select_columns(
    dataset_id: uuid.UUID,
    req: SelectColumnsRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    output_name = req.output_name or f"{dataset.name} (subset)"
    new_ds, dropped = await ds_service.select_columns_dataset(
        db, dataset, org_id=current.org_id, created_by=current.id, columns=req.columns, output_name=output_name
    )
    await log_event(
        db,
        action="dataset.transformed",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(new_ds.id),
        metadata={"source": str(dataset.id), "op": "select-columns"},
    )
    await db.commit()
    await db.refresh(new_ds)
    return SelectColumnsResponse(dataset=_to_response(new_ds), dropped_columns=dropped)


@router.post("/{dataset_id}/transform/pivot", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def pivot_dataset(
    dataset_id: uuid.UUID,
    req: PivotRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    output_name = req.output_name or f"{dataset.name} ({req.direction})"
    new_ds = await ds_service.pivot_dataset(
        db, dataset, org_id=current.org_id, created_by=current.id, req=req, output_name=output_name
    )
    await log_event(
        db,
        action="dataset.transformed",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(new_ds.id),
        metadata={"source": str(dataset.id), "op": f"pivot:{req.direction}"},
    )
    await db.commit()
    await db.refresh(new_ds)
    return _to_response(new_ds)


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
