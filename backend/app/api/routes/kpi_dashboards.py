"""Named, ordered, tiered collections of KPIs for the dashboard view. See
app/services/kpis.py's compute_kpis_multi_dataset for why cards computes
each KPI against its own dataset_id instead of one shared one.
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
from app.services import kpis as kpi_service

router = APIRouter(prefix="/kpi-dashboards", tags=["kpi-dashboards"])

_DEFAULT_REFRESH = {"operator": 15, "supervisor": 300}


class KpiDashboardWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    tier: str = "supervisor"  # operator | supervisor
    kpi_ids: list[uuid.UUID] = Field(default_factory=list)
    refresh_seconds: int | None = Field(default=None, ge=1)


class KpiDashboardPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tier: str | None = None
    kpi_ids: list[uuid.UUID] | None = None
    refresh_seconds: int | None = Field(default=None, ge=1)


class KpiDashboardResponse(BaseModel):
    id: str
    name: str
    tier: str
    kpi_ids: list[str]
    refresh_seconds: int
    created_at: str
    updated_at: str


def _validate_tier(tier: str) -> None:
    if tier not in ("operator", "supervisor"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tier must be 'operator' or 'supervisor'")


def _to_response(d: KpiDashboard) -> KpiDashboardResponse:
    return KpiDashboardResponse(
        id=str(d.id), name=d.name, tier=d.tier, kpi_ids=[str(k) for k in d.kpi_ids],
        refresh_seconds=d.refresh_seconds, created_at=d.created_at.isoformat(), updated_at=d.updated_at.isoformat(),
    )


@router.get("", response_model=list[KpiDashboardResponse])
async def list_dashboards(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(KpiDashboard).where(KpiDashboard.org_id == current.org_id).order_by(KpiDashboard.name))
    ).scalars().all()
    return [_to_response(d) for d in rows]


@router.post("", response_model=KpiDashboardResponse, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    req: KpiDashboardWriteRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    _validate_tier(req.tier)
    dashboard = KpiDashboard(
        org_id=current.org_id,
        name=req.name,
        tier=req.tier,
        kpi_ids=[str(k) for k in dict.fromkeys(req.kpi_ids)],
        refresh_seconds=req.refresh_seconds or _DEFAULT_REFRESH[req.tier],
        created_by=current.id,
    )
    db.add(dashboard)
    await log_event(db, action="kpi_dashboard.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name})
    await db.commit()
    await db.refresh(dashboard)
    return _to_response(dashboard)


@router.patch("/{dashboard_id}", response_model=KpiDashboardResponse)
async def update_dashboard(
    dashboard_id: uuid.UUID,
    patch: KpiDashboardPatchRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    updates = patch.model_dump(exclude_unset=True)
    if "tier" in updates:
        _validate_tier(updates["tier"])
    if "kpi_ids" in updates:
        updates["kpi_ids"] = [str(k) for k in dict.fromkeys(patch.kpi_ids)]
    for field_name, value in updates.items():
        setattr(dashboard, field_name, value)
    await log_event(db, action="kpi_dashboard.updated", org_id=current.org_id, actor_user_id=current.id, target=str(dashboard.id))
    await db.commit()
    await db.refresh(dashboard)
    return _to_response(dashboard)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    await db.delete(dashboard)
    await log_event(db, action="kpi_dashboard.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(dashboard_id))
    await db.commit()


class KpiDashboardCardResponse(BaseModel):
    kpi_id: str
    dataset_id: str
    name: str
    unit: str | None
    direction: str
    value: float | None
    meta: dict
    computed_at: str
    cache_hit: bool
    error: str | None
    eval: dict | None
    breakdown: list[dict] | None


@router.get("/{dashboard_id}/cards", response_model=list[KpiDashboardCardResponse])
async def dashboard_cards(
    dashboard_id: uuid.UUID,
    force: bool = False,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    kpi_ids = [uuid.UUID(k) for k in dashboard.kpi_ids]
    if not kpi_ids:
        return []

    kpi_defs = {
        k.id: k
        for k in (await db.execute(select(Kpi).where(Kpi.org_id == current.org_id, Kpi.id.in_(kpi_ids)))).scalars().all()
    }
    items = await kpi_service.compute_kpis_multi_dataset(
        db, current.org_id, kpi_ids, force=force, include_eval=True, lookback=14,
    )

    out: list[KpiDashboardCardResponse] = []
    for item in items:
        kdef = kpi_defs.get(item.kpi_id)
        out.append(
            KpiDashboardCardResponse(
                kpi_id=str(item.kpi_id),
                dataset_id=str(kdef.dataset_id) if kdef else "",
                name=kdef.name if kdef else "Unknown KPI",
                unit=kdef.unit if kdef else None,
                direction=kdef.direction if kdef else "neutral",
                value=item.value, meta=item.meta, computed_at=item.computed_at.isoformat(),
                cache_hit=item.cache_hit, error=item.error, eval=item.eval,
                breakdown=[{"label": label, "value": v} for label, v in item.breakdown] if item.breakdown else None,
            )
        )
    return out
