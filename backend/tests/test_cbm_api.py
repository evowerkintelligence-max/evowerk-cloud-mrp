from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.models.sync_schedule import SyncSchedule

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


async def _create_postgres_integration(client, headers):
    res = await client.post(
        "/integrations", headers=headers,
        json={
            "name": "Cloud DB", "type": "postgres",
            "config": {"host": "db.example.com", "database": "prod", "username": "u", "password": "p", "query": "SELECT * FROM ticks"},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _create_sync_schedule(client, headers, *, machine: str, sensor_map: dict, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    integration_id = await _create_postgres_integration(client, headers)
    res = await client.post(
        "/sync-schedules", headers=headers,
        json={
            "integration_id": integration_id, "name": "Line 1", "interval_seconds": 300,
            "cursor_column": "ts", "machine": machine, "sensor_map": sensor_map,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_fleet_dashboard_shows_stream_status_per_machine(client, monkeypatch):
    token = await _register_and_login(client)
    await _create_sync_schedule(client, _h(token), machine="M1", sensor_map={"vib_x": "v"}, monkeypatch=monkeypatch)

    res = await client.get("/cbm/fleet", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    machines = {m["machine"]: m for m in body["machines"]}
    assert machines["M1"]["status"] == "learning"  # no readings yet -> learning, not "active"


async def test_fleet_dashboard_flags_unmapped_stream(client, monkeypatch):
    token = await _register_and_login(client)
    schedule = await _create_sync_schedule(client, _h(token), machine="M1", sensor_map={}, monkeypatch=monkeypatch)

    res = await client.get("/cbm/fleet", headers=_h(token))
    assert res.status_code == 200, res.text
    machines = {m["machine"]: m for m in res.json()["machines"]}
    assert machines["M1"]["status"] == "unmapped"
    assert machines["M1"]["risk_level"] == "unknown"
    assert machines["M1"]["failure_probability_30d"] is None
    assert machines["M1"]["active_anomalies"] == 0
    assert machines["M1"]["last_reading_at"] is None
    assert machines["M1"]["schedule_id"] == schedule["schedule_id"]


async def test_machine_detail_returns_readings_and_anomalies(client, monkeypatch):
    token = await _register_and_login(client)
    await _create_sync_schedule(client, _h(token), machine="M1", sensor_map={"vib_x": "v"}, monkeypatch=monkeypatch)

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(SyncSchedule.org_id).where(SyncSchedule.machine == "M1"))
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


async def test_cbm_tenant_isolation(client, monkeypatch):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    await _create_sync_schedule(client, _h(token_a), machine="M1", sensor_map={"vib_x": "v"}, monkeypatch=monkeypatch)

    res_b = await client.get("/cbm/fleet", headers=_h(token_b))
    assert res_b.status_code == 200
    assert res_b.json()["machines"] == []


async def test_acknowledge_anomaly(client, monkeypatch):
    token = await _register_and_login(client)
    await _create_sync_schedule(client, _h(token), machine="M1", sensor_map={"vib_x": "v"}, monkeypatch=monkeypatch)

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(SyncSchedule.org_id).where(SyncSchedule.machine == "M1"))
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
