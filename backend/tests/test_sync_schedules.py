from __future__ import annotations

import uuid

import pytest

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


@pytest.mark.asyncio
async def test_create_schedule_registers_it_and_returns_snapshot(client, monkeypatch):
    from app.services import sync_scheduler
    added = []
    monkeypatch.setattr(sync_scheduler, "add_job", lambda sid, interval: added.append((sid, interval)))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)

    res = await client.post(
        "/sync-schedules", headers=headers,
        json={
            "integration_id": integration_id, "name": "Line 1", "interval_seconds": 300,
            "cursor_column": "ts", "column_map": {"units_col": "units"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] in ("stopped", "not_synced_yet")
    assert len(added) == 1


@pytest.mark.asyncio
async def test_create_schedule_rejects_connector_without_incremental_support(client):
    token = await _register_and_login(client)
    headers = _h(token)
    integ = await client.post(
        "/integrations", headers=headers,
        json={"name": "MQTT feed", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201
    integration_id = integ.json()["id"]

    res = await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Bad", "interval_seconds": 300, "cursor_column": "ts"},
    )
    assert res.status_code == 400
    assert "does not support" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_schedules_attaches_alerts_to_the_right_schedule(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)

    a = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "A", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]
    b = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "B", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    rule = await client.post(
        "/alerts/rules", headers=headers,
        json={
            "name": "A watch", "type": "stream_metric",
            "config": {"schedule_id": a, "metric_name": "throughput", "comparison": "gt", "threshold": -1},
        },
    )
    assert rule.status_code == 201, rule.text

    listed = (await client.get("/sync-schedules", headers=headers)).json()["schedules"]
    by_id = {s["schedule_id"]: s for s in listed}
    assert len(by_id[a]["alerts"]) == 1
    assert by_id[b]["alerts"] == []


@pytest.mark.asyncio
async def test_stop_then_start_reregisters_job(client, monkeypatch):
    from app.services import sync_scheduler
    calls = []
    monkeypatch.setattr(sync_scheduler, "add_job", lambda sid, interval: calls.append(("add", sid)))
    monkeypatch.setattr(sync_scheduler, "remove_job", lambda sid: calls.append(("remove", sid)))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    stop = await client.post(f"/sync-schedules/{schedule_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    start = await client.post(f"/sync-schedules/{schedule_id}/start", headers=headers)
    assert start.status_code == 200

    kinds = [c[0] for c in calls]
    assert kinds.count("add") == 2  # once on create, once on start
    assert kinds.count("remove") == 1  # once on stop


@pytest.mark.asyncio
async def test_delete_schedule_removes_job_and_definition(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)
    removed = []
    monkeypatch.setattr(sync_scheduler, "remove_job", lambda sid: removed.append(sid))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    delete = await client.delete(f"/sync-schedules/{schedule_id}", headers=headers)
    assert delete.status_code == 204
    assert len(removed) == 1

    missing = await client.get(f"/sync-schedules/{schedule_id}/snapshot", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_cannot_access_another_orgs_schedule(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    headers_a = _h(token_a)
    integration_id = await _create_postgres_integration(client, headers_a)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers_a,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = _h(token_b)
    res = await client.get(f"/sync-schedules/{schedule_id}/snapshot", headers=headers_b)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_persisted_ticks(client, monkeypatch):
    from app.db import session as db_session
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    from app.models.sync_tick import SyncTick
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    async with db_session.async_session_factory() as db:
        db.add(SyncTick(schedule_id=uuid.UUID(schedule_id), org_id=uuid.UUID(org_id), row_count=2, metrics={"throughput": 4.0}))
        await db.commit()

    history = await client.get(f"/sync-schedules/{schedule_id}/history", headers=headers)
    assert history.status_code == 200
    assert len(history.json()["points"]) == 1
    assert history.json()["points"][0]["metrics"]["throughput"] == 4.0
