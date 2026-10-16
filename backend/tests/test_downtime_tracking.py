# backend/tests/test_downtime_tracking.py
from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register(client, email: str = "dt@example.com", org_name: str = "DtOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_downtime_incident_model_roundtrip(client, test_engine):
    from app.models.downtime_tracking import DowntimeIncident

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        incident = DowntimeIncident(
            org_id=org_id, started_by=user_id, machine="Line 1", reason="breakdown", shift="A"
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        assert incident.id is not None
        assert incident.started_at is not None
        assert incident.ended_at is None


async def _register_and_login(client, email: str, org_name: str = "DtOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def test_start_incident_then_list_open(client):
    token = await _register_and_login(client, "dtstart@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["machine"] == "Line 1"
    assert body["reason"] == "breakdown"
    assert body["shift"] == "A"
    assert body["ended_at"] is None

    listed = await client.get("/downtime-tracking/incidents?open=true", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


async def test_double_start_same_machine_rejected(client):
    token = await _register_and_login(client, "dtdouble@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "other", "shift": "A"},
    )
    assert res.status_code == 409


async def test_invalid_reason_rejected(client):
    token = await _register_and_login(client, "dtreason@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "nope", "shift": "A"},
    )
    assert res.status_code == 400


async def test_end_incident_creates_live_dataset(client):
    token = await _register_and_login(client, "dtend@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
    assert ended.status_code == 200
    body = ended.json()
    assert body["incident"]["ended_at"] is not None
    assert body["live_dataset_id"]

    open_list = await client.get("/downtime-tracking/incidents?open=true", headers=headers)
    assert open_list.json() == []

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    assert live.json()["dataset_id"] == body["live_dataset_id"]


async def test_machines_and_shifts_autocomplete(client):
    token = await _register_and_login(client, "dtauto@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 2", "reason": "other", "shift": "B"},
    )
    machines = await client.get("/downtime-tracking/machines", headers=headers)
    assert set(machines.json()) == {"Line 1", "Line 2"}
    shifts = await client.get("/downtime-tracking/shifts", headers=headers)
    assert set(shifts.json()) == {"A", "B"}


async def test_downtime_tracking_tenant_isolation(client):
    token_a = await _register_and_login(client, "dta@example.com", "OrgA2")
    token_b = await _register_and_login(client, "dtb@example.com", "OrgB2")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers_a,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    res = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers_b)
    assert res.status_code == 404

    open_b = await client.get("/downtime-tracking/incidents?open=true", headers=headers_b)
    assert open_b.json() == []


async def test_discard_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "dtdiscardowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    session_factory = _asm(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="dtdiscardmember@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "dtdiscardmember@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=member_headers)
    assert res.status_code == 403

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 204


async def test_discard_allowed_for_owner_deleting_someone_elses_incident(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "dt2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = _asm(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="dt2member@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "dt2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=member_headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]

    # the org owner can discard a different member's incident.
    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 204


async def test_discard_rejected_once_closed(client):
    token = await _register_and_login(client, "dtdiscardclosed@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]
    await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)

    res = await client.delete(f"/downtime-tracking/incidents/{incident_id}", headers=headers)
    assert res.status_code == 409


async def test_materialization_accumulates_and_feeds_downtime_analytics(client):
    token = await _register_and_login(client, "dte2e@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _cycle(machine: str, reason: str, shift: str) -> dict:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": reason, "shift": shift},
        )
        incident_id = start.json()["id"]
        await asyncio.sleep(0.05)
        ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
        return ended.json()

    first = await _cycle("Line 1", "breakdown", "A")
    dataset_id = first["live_dataset_id"]

    second = await _cycle("Line 2", "changeover", "B")
    assert second["live_dataset_id"] == dataset_id  # same dataset, not a new one

    third = await _cycle("Line 1", "breakdown", "A")
    assert third["live_dataset_id"] == dataset_id

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    assert live.json()["dataset_id"] == dataset_id

    downtime_res = await client.post(
        "/analytics/downtime", headers=headers,
        json={
            "dataset_id": dataset_id,
            "duration_col": "duration_min",
            "category_col": "reason",
            "machine_col": "machine",
            "timestamp_col": "started_at",
        },
    )
    assert downtime_res.status_code == 200, downtime_res.text
    body = downtime_res.json()
    assert body["total_incidents"] == 3
    cats = {c["category"]: c for c in body["by_category"]}
    assert cats["breakdown"]["incidents"] == 2
    assert cats["changeover"]["incidents"] == 1
    machines = {m["machine"] for m in body["by_machine"]}
    assert machines == {"Line 1", "Line 2"}


async def test_planned_column_lets_downtime_engine_pareto_by_planned(client):
    token = await _register_and_login(client, "dtplanned@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for machine, reason in [("Line 1", "breakdown"), ("Line 2", "changeover"), ("Line 3", "material_shortage")]:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": reason, "shift": "A"},
        )
        await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    dataset_id = live.json()["dataset_id"]

    res = await client.post(
        "/analytics/downtime", headers=headers,
        json={"dataset_id": dataset_id, "duration_col": "duration_min", "category_col": "planned"},
    )
    assert res.status_code == 200, res.text
    cats = {c["category"] for c in res.json()["by_category"]}
    assert cats == {"True", "False"}


async def test_shift_column_feeds_shifts_analytics(client):
    token = await _register_and_login(client, "dtshift@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for machine, shift in [("Line 1", "A"), ("Line 2", "B")]:
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": machine, "reason": "breakdown", "shift": shift},
        )
        await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)

    live = await client.get("/downtime-tracking/live-dataset", headers=headers)
    dataset_id = live.json()["dataset_id"]

    res = await client.post(
        "/analytics/shifts", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift",
            "downtime_col": "duration_min", "downtime_category_col": "reason",
        },
    )
    assert res.status_code == 200, res.text

    # Prove the two incidents actually landed under distinct shift values (not
    # just that the endpoint didn't error) by re-grouping the same dataset's
    # Pareto breakdown with "shift" as the category column.
    by_shift = await client.post(
        "/analytics/downtime", headers=headers,
        json={"dataset_id": dataset_id, "duration_col": "duration_min", "category_col": "shift"},
    )
    assert by_shift.status_code == 200, by_shift.text
    shift_cats = {c["category"]: c["incidents"] for c in by_shift.json()["by_category"]}
    assert shift_cats == {"A": 1, "B": 1}


async def test_repeated_overwrite_produces_correct_row_count(client):
    token = await _register_and_login(client, "dtoverwrite@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    dataset_id = None
    for i in range(5):
        start = await client.post(
            "/downtime-tracking/incidents", headers=headers,
            json={"machine": f"Line {i}", "reason": "breakdown", "shift": "A"},
        )
        ended = await client.post(f"/downtime-tracking/incidents/{start.json()['id']}/end", headers=headers)
        dataset_id = ended.json()["live_dataset_id"]

    ds = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert ds.status_code == 200
    assert ds.json()["row_count"] == 5
