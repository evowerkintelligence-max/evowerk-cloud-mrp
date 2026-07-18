from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading, WeibullFit

pytestmark = pytest.mark.asyncio


async def test_sensor_models_round_trip(db_session):
    org_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=4.2, ts=now)
    db_session.add(reading)
    await db_session.flush()

    baseline = SensorBaseline(
        org_id=org_id, machine="M1", sensor="vib_x", n=25,
        centerline=4.0, ucl=5.0, lcl=3.0, mr_bar=0.3,
    )
    db_session.add(baseline)

    anomaly = SensorAnomaly(
        org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1],
    )
    db_session.add(anomaly)

    fit = WeibullFit(
        org_id=org_id, machine="M1", shape_k=1.8, scale_lambda=45.0,
        n_failures=6, n_censored=1, fit_tier="per_machine",
    )
    db_session.add(fit)
    await db_session.commit()

    got_reading = (await db_session.execute(select(SensorReading).where(SensorReading.org_id == org_id))).scalar_one()
    assert got_reading.sensor == "vib_x"
    got_anomaly = (await db_session.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == org_id))).scalar_one()
    assert got_anomaly.violations == [1]
    got_fit = (await db_session.execute(select(WeibullFit).where(WeibullFit.org_id == org_id))).scalar_one()
    assert got_fit.fit_tier == "per_machine"
