from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick


@pytest.mark.asyncio
async def test_sync_schedule_round_trips_cursor_fields(db_session):
    from app.models.integration import Integration
    from app.models.tenant import Org
    from app.models.user import User

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="a@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(
        org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id,
    )
    db_session.add(integration)
    await db_session.flush()

    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        column_map={"units_col": "units"}, cursor_column="ts", cursor_value=None,
        interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()

    fetched = (
        await db_session.execute(select(SyncSchedule).where(SyncSchedule.id == schedule.id))
    ).scalar_one()
    assert fetched.cursor_column == "ts"
    assert fetched.desired_status == "running"


@pytest.mark.asyncio
async def test_sync_tick_and_sync_row_link_to_schedule(db_session):
    from app.models.integration import Integration
    from app.models.tenant import Org
    from app.models.user import User

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="b@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.flush()

    tick = SyncTick(schedule_id=schedule.id, org_id=org.id, row_count=3, metrics={"throughput": 9.0})
    row = SyncRow(schedule_id=schedule.id, row={"ts": "2026-08-01", "units": 3})
    db_session.add_all([tick, row])
    await db_session.commit()

    fetched_tick = (
        await db_session.execute(select(SyncTick).where(SyncTick.schedule_id == schedule.id))
    ).scalar_one()
    assert fetched_tick.metrics["throughput"] == 9.0
    fetched_row = (
        await db_session.execute(select(SyncRow).where(SyncRow.schedule_id == schedule.id))
    ).scalar_one()
    assert fetched_row.row["units"] == 3
