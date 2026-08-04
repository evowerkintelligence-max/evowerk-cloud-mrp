"""Scheduled cloud-DB sync API — periodically pulls new rows from an
Integration's connector on an interval, computing OEE metrics (and, where
configured, CBM sensor scoring / AutoML live scoring) over each batch.
Replaces app.api.routes.realtime's SSE-based live-stream API — see
docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncTick
from app.services import alerts as alerts_service
from app.services import sync_scheduler

router = APIRouter(prefix="/sync-schedules", tags=["sync-schedules"])

# Connector types whose fetch_since() supports incremental cursor-based sync.
# Keep in sync with app.services.connectors.__init__.VALID_TYPES additions.
_INCREMENTAL_TYPES = {"sqlserver", "oracle", "postgres", "mysql"}


class ScheduleCreate(BaseModel):
    integration_id: uuid.UUID
    name: str = ""
    interval_seconds: int = Field(300, ge=60, le=86400)
    cursor_column: str
    column_map: dict[str, str] = Field(default_factory=dict)
    machine: str | None = None
    sensor_map: dict[str, str] = Field(default_factory=dict)


def _snapshot_dict(schedule: SyncSchedule, latest_tick: SyncTick | None, alerts: list) -> dict:
    if latest_tick is None:
        snap = {
            "ts": None, "schedule_id": str(schedule.id), "name": schedule.name,
            "status": "stopped" if schedule.desired_status == "stopped" else "not_synced_yet",
            "error": schedule.last_error,
            "row_count": 0, "metrics": {},
        }
    else:
        snap = {
            "ts": latest_tick.ts.isoformat(), "schedule_id": str(schedule.id), "name": schedule.name,
            "status": "stopped" if schedule.desired_status == "stopped" else "running",
            "error": schedule.last_error,
            "row_count": latest_tick.row_count, "metrics": latest_tick.metrics,
        }
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


async def _latest_tick(db: AsyncSession, schedule_id: uuid.UUID) -> SyncTick | None:
    return (
        await db.execute(
            select(SyncTick).where(SyncTick.schedule_id == schedule_id).order_by(SyncTick.ts.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _snapshot_for(db: AsyncSession, org_id: uuid.UUID, schedule: SyncSchedule) -> dict:
    tick = await _latest_tick(db, schedule.id)
    alerts = await alerts_service.stream_alerts(db, org_id, schedule.id)
    return _snapshot_dict(schedule, tick, alerts)


@router.post("")
async def create_schedule(
    req: ScheduleCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    integration = await get_or_404(db, Integration, current.org_id, req.integration_id, "Integration not found")
    if integration.type not in _INCREMENTAL_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Connector type {integration.type!r} does not support incremental sync "
            f"(supported: {sorted(_INCREMENTAL_TYPES)})",
        )

    schedule = SyncSchedule(
        org_id=current.org_id,
        integration_id=integration.id,
        name=req.name or integration.name,
        column_map=req.column_map,
        machine=req.machine,
        sensor_map=req.sensor_map,
        interval_seconds=req.interval_seconds,
        cursor_column=req.cursor_column,
        desired_status="running",
        created_by=current.id,
    )
    db.add(schedule)
    await log_event(
        db, action="sync_schedule.created", org_id=current.org_id, actor_user_id=current.id,
        metadata={"name": schedule.name, "integration_id": str(integration.id)},
    )
    await db.commit()
    await db.refresh(schedule)

    sync_scheduler.add_job(schedule.id, schedule.interval_seconds)
    return await _snapshot_for(db, current.org_id, schedule)


@router.get("")
async def list_schedules(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(SyncSchedule).where(SyncSchedule.org_id == current.org_id).order_by(SyncSchedule.name))
    ).scalars().all()
    alerts_by_schedule = await alerts_service.stream_alerts_bulk(db, current.org_id, [s.id for s in rows])
    snapshots = []
    for s in rows:
        tick = await _latest_tick(db, s.id)
        snapshots.append(_snapshot_dict(s, tick, alerts_by_schedule.get(str(s.id), [])))
    return {"schedules": snapshots}


@router.get("/{schedule_id}/snapshot")
async def schedule_snapshot(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    return await _snapshot_for(db, current.org_id, schedule)


@router.get("/{schedule_id}/history")
async def schedule_history(
    schedule_id: uuid.UUID,
    limit: int = 2000,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    limit = max(1, min(20_000, limit))
    ticks = (
        await db.execute(
            select(SyncTick).where(SyncTick.schedule_id == schedule_id)
            .order_by(SyncTick.ts.desc()).limit(limit)
        )
    ).scalars().all()
    points = [{"ts": t.ts.isoformat(), "row_count": t.row_count, "metrics": t.metrics} for t in reversed(ticks)]
    return {"schedule_id": str(schedule_id), "points": points}


@router.post("/{schedule_id}/start")
async def start_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    schedule.desired_status = "running"
    schedule.last_error = None
    await db.commit()

    sync_scheduler.add_job(schedule.id, schedule.interval_seconds)
    return await _snapshot_for(db, current.org_id, schedule)


@router.post("/{schedule_id}/stop")
async def stop_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    sync_scheduler.remove_job(schedule.id)
    schedule.desired_status = "stopped"
    await db.commit()
    return await _snapshot_for(db, current.org_id, schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    sync_scheduler.remove_job(schedule.id)
    await db.delete(schedule)
    await log_event(db, action="sync_schedule.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(schedule_id))
    await db.commit()
