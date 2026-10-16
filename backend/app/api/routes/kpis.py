"""KPIs — named SQL metrics over a Dataset with cached history and optional
delta/static/baseline/trend evaluation. Ported from the desktop app's
backend/routes/kpis.py; compute/compare/eval logic lives in app.services.kpis.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.kpi import Kpi, KpiDashboard
from app.services import datasets as ds_service
from app.services import kpis as kpi_service
from app.api.routes.kpis_helpers import (
    KpiWriteRequest,
    KpiPatchRequest,
    KpiResponse,
    KpiValueResponse,
    KpiComputeRequest,
    KpiComputeItemResponse,
    KpiCompareRequest,
    KpiCompareItemResponse,
    _kpi_to_response,
    _value_to_response,
    _item_to_response,
    _compare_item_to_response,
    _validate_breakdown,
)

router = APIRouter(prefix="/kpis", tags=["kpis"])


# -----------------------------
# KPI definitions
# -----------------------------

@router.get("", response_model=list[KpiResponse])
async def list_kpis(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Kpi).where(Kpi.org_id == current.org_id).order_by(Kpi.name))
    ).scalars().all()
    return [_kpi_to_response(k) for k in rows]


@router.post("", response_model=KpiResponse, status_code=status.HTTP_201_CREATED)
async def create_kpi(
    req: KpiWriteRequest, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    _validate_breakdown(dataset, req.breakdown_group_col, req.breakdown_agg, req.breakdown_value_col)
    kpi = Kpi(
        org_id=current.org_id,
        dataset_id=req.dataset_id,
        name=req.name,
        description=req.description,
        sql=req.sql,
        unit=req.unit,
        direction=req.direction,
        enabled=req.enabled,
        eval_policy=req.eval_policy.model_dump(exclude_none=True) if req.eval_policy else None,
        tags=req.tags,
        breakdown_group_col=req.breakdown_group_col,
        breakdown_agg=req.breakdown_agg,
        breakdown_value_col=req.breakdown_value_col,
        created_by=current.id,
    )
    db.add(kpi)
    await log_event(db, action="kpi.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name})
    await db.commit()
    await db.refresh(kpi)
    return _kpi_to_response(kpi)


@router.get("/{kpi_id}", response_model=KpiResponse)
async def get_kpi(
    kpi_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    kpi = await get_or_404(db, Kpi, current.org_id, kpi_id, "KPI not found")
    return _kpi_to_response(kpi)


@router.patch("/{kpi_id}", response_model=KpiResponse)
async def update_kpi(
    kpi_id: uuid.UUID,
    patch: KpiPatchRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    kpi = await get_or_404(db, Kpi, current.org_id, kpi_id, "KPI not found")
    updates = patch.model_dump(exclude_unset=True)

    if "dataset_id" in updates:
        await ds_service.get_dataset_or_404(db, current.org_id, patch.dataset_id)
    if "eval_policy" in updates:
        updates["eval_policy"] = patch.eval_policy.model_dump(exclude_none=True) if patch.eval_policy else None

    if {"breakdown_group_col", "breakdown_agg", "breakdown_value_col"} & updates.keys():
        dataset = await ds_service.get_dataset_or_404(db, current.org_id, patch.dataset_id or kpi.dataset_id)
        _validate_breakdown(
            dataset,
            updates.get("breakdown_group_col", kpi.breakdown_group_col),
            updates.get("breakdown_agg", kpi.breakdown_agg),
            updates.get("breakdown_value_col", kpi.breakdown_value_col),
        )

    for field_name, value in updates.items():
        setattr(kpi, field_name, value)

    await log_event(db, action="kpi.updated", org_id=current.org_id, actor_user_id=current.id, target=str(kpi.id))
    await db.commit()
    await db.refresh(kpi)
    return _kpi_to_response(kpi)


@router.delete("/{kpi_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kpi(
    kpi_id: uuid.UUID, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
):
    kpi = await get_or_404(db, Kpi, current.org_id, kpi_id, "KPI not found")
    await db.delete(kpi)

    # kpi_ids is a JSON list column (no portable containment query across
    # SQLite/Postgres) — dashboard/KPI counts are small here, so a full scan
    # and filter in Python is fine.
    kpi_id_str = str(kpi_id)
    dashboards = (
        await db.execute(select(KpiDashboard).where(KpiDashboard.org_id == current.org_id))
    ).scalars().all()
    for dashboard in dashboards:
        if kpi_id_str in dashboard.kpi_ids:
            dashboard.kpi_ids = [k for k in dashboard.kpi_ids if k != kpi_id_str]

    await log_event(db, action="kpi.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(kpi_id))
    await db.commit()


# -----------------------------
# History
# -----------------------------

@router.get("/{kpi_id}/history", response_model=list[KpiValueResponse])
async def kpi_history(
    kpi_id: uuid.UUID,
    dataset_id: uuid.UUID,
    limit: int = 50,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Kpi, current.org_id, kpi_id, "KPI not found")
    limit = max(1, min(limit, 500))
    rows = await kpi_service.get_history_values(db, current.org_id, kpi_id, dataset_id, limit)
    return [_value_to_response(v) for v in rows]


# -----------------------------
# Compute / compare
# -----------------------------

@router.post("/compute", response_model=list[KpiComputeItemResponse])
async def compute_kpis(
    req: KpiComputeRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    items = await kpi_service.compute_kpis(
        db, current.org_id, req.dataset_id, req.kpi_ids,
        force=req.force, include_eval=req.include_eval, lookback=req.lookback,
    )
    return [_item_to_response(i) for i in items]


class KpiCardsRequest(BaseModel):
    """Convenience endpoint for "KPI cards": if kpi_ids is omitted, computes
    every enabled KPI defined against this dataset."""

    dataset_id: uuid.UUID
    kpi_ids: list[uuid.UUID] | None = None
    force: bool = False
    include_eval: bool = True
    lookback: int = Field(7, ge=1, le=3650)


@router.post("/cards", response_model=list[KpiComputeItemResponse])
async def kpi_cards(
    req: KpiCardsRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if req.kpi_ids is not None:
        kpi_ids = req.kpi_ids
    else:
        rows = (
            await db.execute(
                select(Kpi.id).where(Kpi.org_id == current.org_id, Kpi.dataset_id == req.dataset_id, Kpi.enabled.is_(True))
            )
        ).scalars().all()
        kpi_ids = list(rows)

    if not kpi_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No KPI ids to compute (none provided and no enabled KPIs found)")

    items = await kpi_service.compute_kpis(
        db, current.org_id, req.dataset_id, kpi_ids,
        force=req.force, include_eval=req.include_eval, lookback=req.lookback,
    )
    return [_item_to_response(i) for i in items]


@router.post("/compare", response_model=list[KpiCompareItemResponse])
async def compare_kpis(
    req: KpiCompareRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    items = await kpi_service.compare_kpis(
        db, current.org_id, req.dataset_id_a, req.dataset_id_b, req.kpi_ids,
        force=req.force, include_eval=req.include_eval, lookback=req.lookback,
    )
    return [_compare_item_to_response(i) for i in items]


# -----------------------------
# Manufacturing KPI seed templates
# -----------------------------
# Datasets are stored ALL_VARCHAR (see app.services.datasets.relation_for) so
# numeric coercion is left to the query layer uniformly — every aggregated
# column here is TRY_CAST to DOUBLE for the same reason every other
# hand-written engine query in this codebase casts before aggregating.

_MFG_TEMPLATES: list[dict] = [
    {
        "name": "Throughput (units/hr)",
        "description": "Total units produced per operating hour. Columns: units_produced, operating_hours",
        "sql": "SELECT SUM(TRY_CAST(units_produced AS DOUBLE)) / NULLIF(SUM(TRY_CAST(operating_hours AS DOUBLE)), 0) FROM {table}",
        "unit": "units/hr",
        "direction": "higher_is_better",
        "tags": ["manufacturing", "throughput"],
    },
    {
        "name": "First Pass Yield (%)",
        "description": "Units passing quality on first attempt. Columns: good_units, total_units",
        "sql": "SELECT (SUM(TRY_CAST(good_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
        "unit": "%",
        "direction": "higher_is_better",
        "tags": ["manufacturing", "quality"],
    },
    {
        "name": "Scrap Rate (%)",
        "description": "Percentage of production scrapped. Columns: scrap_units, total_units",
        "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
        "unit": "%",
        "direction": "lower_is_better",
        "tags": ["manufacturing", "quality"],
    },
    {
        "name": "Defect Rate (per 1000)",
        "description": "Defects per 1000 units produced. Columns: defects, total_units",
        "sql": "SELECT (SUM(TRY_CAST(defects AS DOUBLE)) * 1000.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
        "unit": "per 1000",
        "direction": "lower_is_better",
        "tags": ["manufacturing", "quality"],
    },
    {
        "name": "Avg Cycle Time (min)",
        "description": "Mean time to produce one unit. Column: cycle_time_min",
        "sql": "SELECT AVG(TRY_CAST(cycle_time_min AS DOUBLE)) FROM {table}",
        "unit": "min",
        "direction": "lower_is_better",
        "tags": ["manufacturing", "efficiency"],
    },
    {
        "name": "On-Time Delivery (%)",
        "description": "Orders delivered on schedule. Column: on_time (1/0)",
        "sql": "SELECT (SUM(TRY_CAST(on_time AS DOUBLE)) * 100.0) / NULLIF(COUNT(*), 0) FROM {table}",
        "unit": "%",
        "direction": "higher_is_better",
        "tags": ["manufacturing", "delivery"],
    },
    {
        "name": "Capacity Utilization (%)",
        "description": "Actual vs design output. Columns: actual_output, design_capacity",
        "sql": "SELECT AVG(TRY_CAST(actual_output AS DOUBLE) * 100.0 / NULLIF(TRY_CAST(design_capacity AS DOUBLE), 0)) FROM {table}",
        "unit": "%",
        "direction": "higher_is_better",
        "tags": ["manufacturing", "efficiency"],
    },
    {
        "name": "Downtime Rate (%)",
        "description": "Downtime as fraction of planned time. Columns: downtime_minutes, planned_minutes",
        "sql": "SELECT (SUM(TRY_CAST(downtime_minutes AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(planned_minutes AS DOUBLE)), 0) FROM {table}",
        "unit": "%",
        "direction": "lower_is_better",
        "tags": ["manufacturing", "downtime"],
    },
    {
        "name": "MTTR (hrs)",
        "description": "Mean Time To Repair. Columns: downtime_hrs, failure_count",
        "sql": "SELECT SUM(TRY_CAST(downtime_hrs AS DOUBLE)) / NULLIF(SUM(TRY_CAST(failure_count AS DOUBLE)), 0) FROM {table}",
        "unit": "hrs",
        "direction": "lower_is_better",
        "tags": ["manufacturing", "reliability"],
    },
    {
        "name": "MTBF (hrs)",
        "description": "Mean Time Between Failures. Columns: operating_time_hrs, downtime_hrs, failure_count",
        "sql": "SELECT (SUM(TRY_CAST(operating_time_hrs AS DOUBLE)) - SUM(TRY_CAST(downtime_hrs AS DOUBLE))) / NULLIF(SUM(TRY_CAST(failure_count AS DOUBLE)), 0) FROM {table}",
        "unit": "hrs",
        "direction": "higher_is_better",
        "tags": ["manufacturing", "reliability"],
    },
]


class MfgSeedRequest(BaseModel):
    dataset_id: uuid.UUID
    skip_existing: bool = True


class MfgSeedResponse(BaseModel):
    created: int
    skipped: int
    templates: list[dict]


@router.post("/seed/manufacturing", response_model=MfgSeedResponse)
async def seed_manufacturing_kpis(
    req: MfgSeedRequest, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
):
    """Create pre-built manufacturing KPI templates for a dataset. SQL uses
    common column name conventions — edit after seeding if your columns differ."""
    await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)

    existing_names = set(
        (await db.execute(select(Kpi.name).where(Kpi.org_id == current.org_id, Kpi.dataset_id == req.dataset_id))).scalars().all()
    )

    created = 0
    skipped = 0
    seeded: list[dict] = []
    for tmpl in _MFG_TEMPLATES:
        if req.skip_existing and tmpl["name"] in existing_names:
            skipped += 1
            continue
        kpi = Kpi(
            org_id=current.org_id,
            dataset_id=req.dataset_id,
            name=tmpl["name"],
            description=tmpl["description"],
            sql=tmpl["sql"],
            unit=tmpl["unit"],
            direction=tmpl["direction"],
            tags=tmpl["tags"],
            created_by=current.id,
        )
        db.add(kpi)
        created += 1
        seeded.append({"name": tmpl["name"]})

    await log_event(
        db, action="kpi.seeded_manufacturing", org_id=current.org_id, actor_user_id=current.id,
        metadata={"created": created, "skipped": skipped},
    )
    await db.commit()
    return MfgSeedResponse(created=created, skipped=skipped, templates=seeded)
