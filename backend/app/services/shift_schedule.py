"""Per-org shift schedule (weekly-recurring start/end time per shift label) —
powers the Shifts page's "current shift" resolution. See app.models.shift_schedule.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shift_schedule import ShiftSchedule


async def list_schedules(db: AsyncSession, org_id: uuid.UUID) -> list[ShiftSchedule]:
    rows = (
        await db.execute(select(ShiftSchedule).where(ShiftSchedule.org_id == org_id).order_by(ShiftSchedule.shift_label))
    ).scalars().all()
    return list(rows)


async def replace_schedules(db: AsyncSession, org_id: uuid.UUID, items: list[dict]) -> list[ShiftSchedule]:
    """Delete-then-insert for the org — simplest correct semantics for a
    small per-org list edited as a whole from a single settings form."""
    await db.execute(delete(ShiftSchedule).where(ShiftSchedule.org_id == org_id))
    rows = [
        ShiftSchedule(
            org_id=org_id,
            shift_label=item["shift_label"],
            start_time=item["start_time"],
            end_time=item["end_time"],
        )
        for item in items
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows
