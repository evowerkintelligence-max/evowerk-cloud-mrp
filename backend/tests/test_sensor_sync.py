from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import Integration
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.models.sync_schedule import SyncSchedule
from app.models.tenant import Org
from app.models.user import User
from app.services import sensor_sync
from tests._test_secrets import WRONG_PASSWORD


async def _make_schedule(db_session, *, sensor_map: dict, machine: str = "M1") -> SyncSchedule:
    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", hashed_password=WRONG_PASSWORD)
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        machine=machine, sensor_map=sensor_map, cursor_column="ts",
        interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)
    return schedule


@pytest.mark.asyncio
async def test_process_rows_writes_sensor_readings(db_session):
    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    rows = [{"vibration_x": 1.2}, {"vibration_x": 1.4}]

    await sensor_sync.process_rows(db_session, schedule, rows)
    await db_session.commit()

    readings = (
        await db_session.execute(
            select(SensorReading).where(SensorReading.org_id == schedule.org_id, SensorReading.sensor == "vib_x")
        )
    ).scalars().all()
    assert len(readings) == 2
    assert {r.value for r in readings} == {1.2, 1.4}

    baseline = (
        await db_session.execute(
            select(SensorBaseline).where(SensorBaseline.org_id == schedule.org_id, SensorBaseline.sensor == "vib_x")
        )
    ).scalar_one()
    assert baseline.n == 2


@pytest.mark.asyncio
async def test_process_rows_skips_missing_or_non_numeric_values(db_session):
    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    rows = [{"vibration_x": None}, {"vibration_x": "not-a-number"}, {}]

    await sensor_sync.process_rows(db_session, schedule, rows)
    await db_session.commit()

    readings = (
        await db_session.execute(select(SensorReading).where(SensorReading.org_id == schedule.org_id))
    ).scalars().all()
    assert readings == []


@pytest.mark.asyncio
async def test_process_rows_flags_out_of_control_reading(db_session):
    from app.analytics.spc_calc import MIN_BASELINE

    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    stable_rows = [{"vibration_x": 1.0} for _ in range(MIN_BASELINE)]
    await sensor_sync.process_rows(db_session, schedule, stable_rows)
    await db_session.commit()

    spike_rows = [{"vibration_x": 500.0}]
    await sensor_sync.process_rows(db_session, schedule, spike_rows)
    await db_session.commit()

    anomalies = (
        await db_session.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == schedule.org_id))
    ).scalars().all()
    assert len(anomalies) >= 1
