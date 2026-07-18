"""Maintenance schedule CRUD - the recommendation/action queue used by both
the (now-removed) OEE-based risk scoring and the CBM fleet dashboard
(app.services.cbm). Kept generic: it doesn't care how a recommendation's
priority/action was computed."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance_schedule import MaintenanceSchedule


async def list_schedules(db: AsyncSession, org_id: uuid.UUID) -> list[MaintenanceSchedule]:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id).order_by(MaintenanceSchedule.created_at.desc())
    )
    return list(result.scalars().all())


async def create_schedule(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, **fields) -> MaintenanceSchedule:
    item = MaintenanceSchedule(org_id=org_id, created_by=created_by, **fields)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def bulk_create_schedules(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, items: list[dict]) -> list[MaintenanceSchedule]:
    rows = [MaintenanceSchedule(org_id=org_id, created_by=created_by, **item) for item in items]
    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


async def get_schedule_or_404(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> MaintenanceSchedule:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id, MaintenanceSchedule.id == schedule_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ValueError("Maintenance schedule item not found")
    return item


async def update_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID, patch: dict) -> MaintenanceSchedule:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    for key, value in patch.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


async def delete_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    await db.delete(item)
    await db.commit()
