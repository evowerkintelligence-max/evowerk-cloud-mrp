from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorReading
from app.services.sensor_ingest import ingest_rows

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


async def _make_sensor_stream(client, token) -> str:
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
    return stream_res.json()["stream_id"]


async def test_ingest_rows_writes_reading_and_flags_anomaly(client):
    token = await _register_and_login(client)
    stream_id = await _make_sensor_stream(client, token)

    # 24 stable readings to build the baseline, then one spike - mirrors
    # test_anomaly.py's out-of-control-spike case. Seeded jitter instead of
    # bit-identical values: 24 identical readings give mr_bar=0 and Nelson
    # rule 7 fires spuriously (see test_anomaly.py's stable-series case).
    random.seed(42)
    for i in range(24):
        value = round(10.0 + random.gauss(0, 0.15), 3)
        async with db_session.async_session_factory() as db:
            stream = (
                await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
            ).scalar_one()
            await ingest_rows(db, stream, [{"vibration_x_mm_s": value}])
            await db.commit()

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        await ingest_rows(db, stream, [{"vibration_x_mm_s": 50.0}])
        await db.commit()

        readings = (await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))).scalars().all()
        assert len(readings) == 25

        anomalies = (await db.execute(select(SensorAnomaly).where(SensorAnomaly.machine == "M1"))).scalars().all()
        assert len(anomalies) == 1
        assert 1 in anomalies[0].violations


async def test_ingest_rows_noop_without_sensor_map(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Plain", "type": "mqtt", "config": {"broker": "test", "topic": "#"}},
    )
    stream_res = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "No sensors"},
    )
    stream_id = stream_res.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        await ingest_rows(db, stream, [{"vibration_x_mm_s": 1.0}])
        await db.commit()
        readings = (await db.execute(select(SensorReading))).scalars().all()
        assert readings == []
