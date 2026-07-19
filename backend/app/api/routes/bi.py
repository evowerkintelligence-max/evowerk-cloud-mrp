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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
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


# ─── Charts ─────────────────────────────────────────────────────────────────

@router.get("/charts", response_model=list[ChartResponse])
async def list_charts(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Chart).where(Chart.org_id == current.org_id).order_by(Chart.name)
        )
    ).scalars().all()
    return [_chart_to_response(c) for c in rows]


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
    position = 0
    if req.dashboard_id is not None:
        # Locks the dashboard row so two concurrent chart-saves onto it can't
        # both read the same max(position) and collide on the same slot.
        await get_or_404(
            db, Dashboard, current.org_id, req.dashboard_id, "Dashboard not found", for_update=True
        )
        # Append after the last chart on this dashboard, not always 0 - two
        # charts both at position 0 make the dashboard's move-up/move-down
        # swap a no-op between them.
        max_position = await db.scalar(
            select(func.max(Chart.position)).where(Chart.dashboard_id == req.dashboard_id)
        )
        position = (max_position + 1) if max_position is not None else 0

    chart = Chart(
        org_id=current.org_id,
        dashboard_id=req.dashboard_id,
        dataset_id=req.dataset_id,
        name=req.name,
        chart_type=req.chart_type,
        query_config=req.query.model_dump(mode="json"),
        category_field=req.category_field,
        value_field=req.value_field,
        position=position,
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
    chart = await get_or_404(db, Chart, current.org_id, chart_id, "Chart not found")
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
    chart = await get_or_404(db, Chart, current.org_id, chart_id, "Chart not found")
    # "dashboard_id" in model_fields_set distinguishes an explicit null (detach
    # from any dashboard) from the field being omitted entirely (leave as-is) -
    # both would otherwise collapse to the same `None` value.
    dashboard_id_sent = "dashboard_id" in req.model_fields_set
    moving_dashboards = dashboard_id_sent and req.dashboard_id != chart.dashboard_id
    if dashboard_id_sent and req.dashboard_id is not None:
        # Locks the dashboard row for the same reason create_chart does: the
        # max(position) read below must not race another chart landing here.
        await get_or_404(
            db, Dashboard, current.org_id, req.dashboard_id, "Dashboard not found", for_update=True
        )
    if req.name is not None:
        chart.name = req.name
    if dashboard_id_sent:
        chart.dashboard_id = req.dashboard_id
    if req.position is not None:
        chart.position = req.position
    elif moving_dashboards and req.dashboard_id is not None:
        # Append at the end of the destination dashboard, matching
        # create_chart, instead of keeping the position from wherever it was.
        max_position = await db.scalar(
            select(func.max(Chart.position)).where(Chart.dashboard_id == req.dashboard_id)
        )
        chart.position = (max_position + 1) if max_position is not None else 0
    await db.commit()
    await db.refresh(chart)
    return _chart_to_response(chart)


@router.delete("/charts/{chart_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chart(
    chart_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    chart = await get_or_404(db, Chart, current.org_id, chart_id, "Chart not found")
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
    dashboard = await get_or_404(db, Dashboard, current.org_id, dashboard_id, "Dashboard not found")
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
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, Dashboard, current.org_id, dashboard_id, "Dashboard not found")
    await db.delete(dashboard)
    await db.commit()
