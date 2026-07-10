"""BI dashboards — a saved chart is a saved structured query
(app.services.dataset_query.DatasetQueryRequest) plus a chart type and which
output columns map to category/value. A dashboard is a named group of charts.
No new query engine: chart data is always computed live by replaying
query_config through the existing dataset_query.run_query.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.dashboard import Chart, Dashboard
from app.services import dataset_query as query_service
from app.services import datasets as ds_service

router = APIRouter(prefix="/bi", tags=["bi"])

CHART_TYPES = ("bar", "line", "pie")


class ChartPreviewRequest(BaseModel):
    dataset_id: uuid.UUID
    chart_type: str
    query: query_service.DatasetQueryRequest
    category_field: str
    value_field: str


class ChartDataResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    chart_type: str
    category_field: str
    value_field: str


class ChartCreateRequest(ChartPreviewRequest):
    name: str
    dashboard_id: uuid.UUID | None = None


class ChartUpdateRequest(BaseModel):
    name: str | None = None
    dashboard_id: uuid.UUID | None = None
    position: int | None = None


class ChartResponse(BaseModel):
    id: str
    dashboard_id: str | None
    dataset_id: str
    name: str
    chart_type: str
    query_config: dict
    category_field: str
    value_field: str
    position: int
    created_at: str


class DashboardCreateRequest(BaseModel):
    name: str


class DashboardResponse(BaseModel):
    id: str
    name: str
    created_at: str


class DashboardDetailResponse(DashboardResponse):
    charts: list[ChartResponse]


def _chart_to_response(c: Chart) -> ChartResponse:
    return ChartResponse(
        id=str(c.id),
        dashboard_id=str(c.dashboard_id) if c.dashboard_id else None,
        dataset_id=str(c.dataset_id),
        name=c.name,
        chart_type=c.chart_type,
        query_config=c.query_config,
        category_field=c.category_field,
        value_field=c.value_field,
        position=c.position,
        created_at=c.created_at.isoformat(),
    )


def _dashboard_to_response(d: Dashboard) -> DashboardResponse:
    return DashboardResponse(id=str(d.id), name=d.name, created_at=d.created_at.isoformat())


def _validate_chart_type(chart_type: str) -> None:
    if chart_type not in CHART_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"chart_type must be one of {CHART_TYPES}")


async def _get_chart_or_404(db: AsyncSession, org_id: uuid.UUID, chart_id: uuid.UUID) -> Chart:
    chart = (
        await db.execute(select(Chart).where(Chart.id == chart_id, Chart.org_id == org_id))
    ).scalar_one_or_none()
    if chart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chart not found")
    return chart


async def _get_dashboard_or_404(db: AsyncSession, org_id: uuid.UUID, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        await db.execute(select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.org_id == org_id))
    ).scalar_one_or_none()
    if dashboard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dashboard not found")
    return dashboard


# ─── Charts ─────────────────────────────────────────────────────────────────

@router.post("/charts/preview", response_model=ChartDataResponse)
async def preview_chart(
    req: ChartPreviewRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _validate_chart_type(req.chart_type)
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    result = await query_service.run_query(db, dataset, current.org_id, req.query)
    return ChartDataResponse(
        columns=result["columns"], rows=result["rows"],
        chart_type=req.chart_type, category_field=req.category_field, value_field=req.value_field,
    )


@router.post("/charts", response_model=ChartResponse, status_code=status.HTTP_201_CREATED)
async def create_chart(
    req: ChartCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _validate_chart_type(req.chart_type)
    await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    if req.dashboard_id is not None:
        await _get_dashboard_or_404(db, current.org_id, req.dashboard_id)

    chart = Chart(
        org_id=current.org_id,
        dashboard_id=req.dashboard_id,
        dataset_id=req.dataset_id,
        name=req.name,
        chart_type=req.chart_type,
        query_config=req.query.model_dump(mode="json"),
        category_field=req.category_field,
        value_field=req.value_field,
        position=0,
        created_by=current.id,
    )
    db.add(chart)
    await db.commit()
    await db.refresh(chart)
    return _chart_to_response(chart)


@router.get("/charts/{chart_id}/data", response_model=ChartDataResponse)
async def get_chart_data(
    chart_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chart = await _get_chart_or_404(db, current.org_id, chart_id)
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, chart.dataset_id)
    query = query_service.DatasetQueryRequest.model_validate(chart.query_config)
    result = await query_service.run_query(db, dataset, current.org_id, query)
    return ChartDataResponse(
        columns=result["columns"], rows=result["rows"],
        chart_type=chart.chart_type, category_field=chart.category_field, value_field=chart.value_field,
    )


@router.patch("/charts/{chart_id}", response_model=ChartResponse)
async def update_chart(
    chart_id: uuid.UUID,
    req: ChartUpdateRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chart = await _get_chart_or_404(db, current.org_id, chart_id)
    if req.dashboard_id is not None:
        await _get_dashboard_or_404(db, current.org_id, req.dashboard_id)
    if req.name is not None:
        chart.name = req.name
    if req.dashboard_id is not None:
        chart.dashboard_id = req.dashboard_id
    if req.position is not None:
        chart.position = req.position
    await db.commit()
    await db.refresh(chart)
    return _chart_to_response(chart)


@router.delete("/charts/{chart_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chart(
    chart_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chart = await _get_chart_or_404(db, current.org_id, chart_id)
    await db.delete(chart)
    await db.commit()


# ─── Dashboards ─────────────────────────────────────────────────────────────

@router.get("/dashboards", response_model=list[DashboardResponse])
async def list_dashboards(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Dashboard).where(Dashboard.org_id == current.org_id).order_by(Dashboard.created_at.desc())
        )
    ).scalars().all()
    return [_dashboard_to_response(d) for d in rows]


@router.post("/dashboards", response_model=DashboardResponse, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    req: DashboardCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dashboard = Dashboard(org_id=current.org_id, name=req.name, created_by=current.id)
    db.add(dashboard)
    await db.commit()
    await db.refresh(dashboard)
    return _dashboard_to_response(dashboard)


@router.get("/dashboards/{dashboard_id}", response_model=DashboardDetailResponse)
async def get_dashboard(
    dashboard_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await _get_dashboard_or_404(db, current.org_id, dashboard_id)
    charts = (
        await db.execute(
            select(Chart).where(Chart.dashboard_id == dashboard.id).order_by(Chart.position, Chart.created_at)
        )
    ).scalars().all()
    return DashboardDetailResponse(
        **_dashboard_to_response(dashboard).model_dump(), charts=[_chart_to_response(c) for c in charts]
    )


@router.delete("/dashboards/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await _get_dashboard_or_404(db, current.org_id, dashboard_id)
    await db.delete(dashboard)
    await db.commit()
