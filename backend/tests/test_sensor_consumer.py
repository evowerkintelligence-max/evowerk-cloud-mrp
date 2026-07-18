from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorReading
from app.services.sensor_consumer import SensorConsumer
from streaming.schemas import Tick

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_handle_tick_writes_sensor_reading_and_flags_anomaly(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201, integ.text

    stream_res = await client.post(
        "/realtime/streams", headers=_h(token),
        json={
            "integration_id": integ.json()["id"], "name": "Line 1 sensors",
            "machine": "M1", "sensor_map": {"vib_x": "vibration_x_mm_s"},
        },
    )
    assert stream_res.status_code == 200, stream_res.text
    stream_id = stream_res.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()

    consumer = SensorConsumer()

    # 24 stable readings to build the baseline, then one spike - mirrors
    # test_anomaly.py's out-of-control-spike case. Small seeded jitter (same
    # as test_anomaly.py's stable-series case) instead of a bit-identical
    # value: scored incrementally (one _handle_tick per tick, unlike
    # test_anomaly.py's single batch call), 24 exactly-identical readings
    # give a zero-variance window (mr_bar=0 -> sigma=0 -> every z-score
    # collapses to 0.0), which spuriously satisfies Nelson rule 7 ("15
    # points within 1 sigma") once n reaches 20. Real sensors never emit
    # bit-identical floats, so this jitter is also the more realistic input.
    random.seed(42)
    for i in range(24):
        value = round(10.0 + random.gauss(0, 0.15), 3)
        tick = Tick(
            stream_id=stream_id, org_id=str(org_id), column_map={}, row={"vibration_x_mm_s": value},
            status="running", error=None, collected_at=datetime.now(timezone.utc).isoformat(),
        )
        await consumer._handle_tick(tick)

    spike_tick = Tick(
        stream_id=stream_id, org_id=str(org_id), column_map={}, row={"vibration_x_mm_s": 50.0},
        status="running", error=None, collected_at=datetime.now(timezone.utc).isoformat(),
    )
    await consumer._handle_tick(spike_tick)

    async with db_session.async_session_factory() as db:
        readings = (
            await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))
        ).scalars().all()
        assert len(readings) == 25

        anomalies = (
            await db.execute(select(SensorAnomaly).where(SensorAnomaly.machine == "M1"))
        ).scalars().all()
        assert len(anomalies) == 1
        assert 1 in anomalies[0].violations
