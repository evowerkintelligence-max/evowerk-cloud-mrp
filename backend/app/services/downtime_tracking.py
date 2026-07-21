# backend/app/services/downtime_tracking.py
"""Live downtime-board lifecycle: start/end/discard an incident, plus
continuous materialization of every closed incident for an org into that
org's one DowntimeLiveDataset - see end_incident/_materialize.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.downtime_tracking import DowntimeIncident, DowntimeLiveDataset
from app.services import datasets as ds_service

REASON_PLANNED = {
    "breakdown": False,
    "changeover": True,
    "material_shortage": False,
    "planned_maintenance": True,
    "quality_hold": False,
    "other": False,
}


async def start_incident(
    db: AsyncSession, *, org_id: uuid.UUID, started_by: uuid.UUID, machine: str, reason: str, shift: str
) -> DowntimeIncident:
    existing = (
        await db.execute(
            select(DowntimeIncident).where(
                DowntimeIncident.org_id == org_id,
                DowntimeIncident.machine == machine,
                DowntimeIncident.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{machine}' already has an open incident")
    incident = DowntimeIncident(org_id=org_id, started_by=started_by, machine=machine, reason=reason, shift=shift)
    db.add(incident)
    await db.flush()
    return incident


async def list_open(db: AsyncSession, org_id: uuid.UUID) -> list[DowntimeIncident]:
    rows = (
        await db.execute(
            select(DowntimeIncident)
            .where(DowntimeIncident.org_id == org_id, DowntimeIncident.ended_at.is_(None))
            .order_by(DowntimeIncident.started_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def list_machines(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = (
        await db.execute(select(DowntimeIncident.machine).where(DowntimeIncident.org_id == org_id).distinct())
    ).scalars().all()
    return sorted(set(rows))


async def list_shifts(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = (
        await db.execute(select(DowntimeIncident.shift).where(DowntimeIncident.org_id == org_id).distinct())
    ).scalars().all()
    return sorted(set(rows))


async def get_live_dataset_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID | None:
    live = (
        await db.execute(select(DowntimeLiveDataset).where(DowntimeLiveDataset.org_id == org_id))
    ).scalar_one_or_none()
    return live.dataset_id if live else None


async def _materialize(
    db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID, background_tasks: "BackgroundTasks | None" = None
) -> uuid.UUID:
    rows = (
        await db.execute(
            select(DowntimeIncident)
            .where(DowntimeIncident.org_id == org_id, DowntimeIncident.ended_at.isnot(None))
            .order_by(DowntimeIncident.started_at)
        )
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["machine", "reason", "planned", "shift", "duration_min", "started_at", "ended_at"])
    for r in rows:
        duration_min = (ensure_utc(r.ended_at) - ensure_utc(r.started_at)).total_seconds() / 60.0
        writer.writerow([
            r.machine, r.reason, REASON_PLANNED[r.reason], r.shift,
            round(duration_min, 4), r.started_at.isoformat(), r.ended_at.isoformat(),
        ])
    csv_bytes = buf.getvalue().encode("utf-8")

    live = (
        await db.execute(select(DowntimeLiveDataset).where(DowntimeLiveDataset.org_id == org_id))
    ).scalar_one_or_none()

    if live is None:
        dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=created_by,
            name="Live Downtime Board", filename="downtime_board.csv", data=csv_bytes,
        )
        db.add(DowntimeLiveDataset(org_id=org_id, dataset_id=dataset.id))
        await db.flush()
        return dataset.id

    dataset = await ds_service.resolve_dataset(db, org_id, str(live.dataset_id))
    await ds_service.overwrite_dataset_file(db, dataset, data=csv_bytes, background_tasks=background_tasks)
    return dataset.id


async def end_incident(
    db: AsyncSession,
    incident: DowntimeIncident,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    background_tasks: "BackgroundTasks | None" = None,
) -> uuid.UUID:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already ended")
    incident.ended_at = datetime.now(timezone.utc)
    await db.flush()
    return await _materialize(db, org_id=org_id, created_by=created_by, background_tasks=background_tasks)


async def discard_incident(db: AsyncSession, incident: DowntimeIncident) -> None:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot discard an incident that has already ended")
    await db.delete(incident)
    await db.flush()
