from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading

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


async def test_fleet_dashboard_shows_stream_status_per_machine(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )
    assert stream.status_code == 200, stream.text

    res = await client.get("/cbm/fleet", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    machines = {m["machine"]: m for m in body["machines"]}
    assert machines["M1"]["status"] == "learning"  # no readings yet -> learning, not "active"


async def test_machine_detail_returns_readings_and_anomalies(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.machine == "M1"))
        ).scalar_one()
        now = datetime.now(timezone.utc)
        reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=50.0, ts=now)
        db.add(reading)
        await db.flush()
        db.add(SensorBaseline(org_id=org_id, machine="M1", sensor="vib_x", n=25, centerline=10.0, ucl=15.0, lcl=5.0, mr_bar=1.0))
        db.add(SensorAnomaly(org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1]))
        await db.commit()

    res = await client.get("/cbm/machines/M1", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sensors"]["vib_x"]["readings"][-1]["value"] == 50.0
    assert len(body["sensors"]["vib_x"]["anomalies"]) == 1


async def test_cbm_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    integ = await client.post(
        "/integrations", headers=_h(token_a),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    await client.post(
        "/realtime/streams", headers=_h(token_a),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )

    res_b = await client.get("/cbm/fleet", headers=_h(token_b))
    assert res_b.status_code == 200
    assert res_b.json()["machines"] == []


async def test_acknowledge_anomaly(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.machine == "M1"))
        ).scalar_one()
        reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=50.0, ts=datetime.now(timezone.utc))
        db.add(reading)
        await db.flush()
        anomaly = SensorAnomaly(org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1])
        db.add(anomaly)
        await db.commit()
        anomaly_id = anomaly.id

    res = await client.post(f"/cbm/anomalies/{anomaly_id}/acknowledge", headers=_h(token))
    assert res.status_code == 200, res.text
    assert res.json()["acknowledged_at"] is not None


async def test_acknowledge_anomaly_not_found(client):
    token = await _register_and_login(client)
    res = await client.post(f"/cbm/anomalies/{uuid.uuid4()}/acknowledge", headers=_h(token))
    assert res.status_code == 404
