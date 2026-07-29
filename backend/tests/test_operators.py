# backend/tests/test_operators.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.capture_event import CaptureEvent
from app.models.work_order import WorkOrder
from app.services.operator_capture import open_capture_event

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

NOW = datetime.now(timezone.utc)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _org_id(client, headers):
    return uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])


async def _seed_work_order(db_session, org_id, operator_id):
    """Minimal WorkOrder row with assigned_operator_id set - other FKs
    (item/bom/location/created_by) are random uuids, same trick used in
    test_operator_capture_service.py's _seed_in_progress_work_order, since
    sqlite in this test suite has no PRAGMA foreign_keys=ON."""
    wo = WorkOrder(
        org_id=org_id, item_id=uuid.uuid4(), bom_id=uuid.uuid4(), wo_number=1,
        status="in_progress", quantity_planned=Decimal("10"), location_id=uuid.uuid4(),
        assigned_operator_id=operator_id, created_by=uuid.uuid4(),
    )
    db_session.add(wo)
    await db_session.flush()
    await db_session.commit()
    return wo


async def test_create_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/operators", json={"name": "J Kim", "code": "E-100"}, headers=headers)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "J Kim"
    assert body["code"] == "E-100"
    assert body["badge_id"] is None
    assert body["status"] == "active"
    assert body["needs_review"] is False


async def test_create_duplicate_name_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "J Kim"}, headers=headers)
    dup = await client.post("/operators", json={"name": "j kim"}, headers=headers)
    assert dup.status_code == 409


async def test_create_duplicate_badge_id_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "A", "badge_id": "B1"}, headers=headers)
    dup = await client.post("/operators", json={"name": "B", "badge_id": "B1"}, headers=headers)
    assert dup.status_code == 409


async def test_list_needs_review_filter(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Johnson"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Jonson"}, headers=headers)).json()

    flagged = await client.get("/operators?needs_review=true", headers=headers)
    assert len(flagged.json()) == 1
    assert flagged.json()[0]["id"] == close["id"]


async def test_patch_operator_clears_needs_review(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Johnson"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Jonson"}, headers=headers)).json()
    assert close["needs_review"] is True

    patched = await client.patch(
        f"/operators/{close['id']}", json={"needs_review": False, "name": "Jonson"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["needs_review"] is False


async def test_merge_deletes_source_and_keeps_target(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    target = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    source = (await client.post("/operators", json={"name": "J. Kim"}, headers=headers)).json()

    merged = await client.post(
        f"/operators/{source['id']}/merge", json={"target_operator_id": target["id"]}, headers=headers
    )
    assert merged.status_code == 200
    assert merged.json()["id"] == target["id"]

    listed = await client.get("/operators", headers=headers)
    ids = {o["id"] for o in listed.json()}
    assert target["id"] in ids
    assert source["id"] not in ids


async def test_merge_into_self_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.post(f"/operators/{op['id']}/merge", json={"target_operator_id": op["id"]}, headers=headers)
    assert res.status_code == 400


async def test_delete_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.delete(f"/operators/{op['id']}", headers=headers)
    assert res.status_code == 204
    listed = await client.get("/operators", headers=headers)
    assert listed.json() == []


async def test_merge_repoints_capture_events_to_target(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    target = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    source = (await client.post("/operators", json={"name": "J. Kim"}, headers=headers)).json()

    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()
    event = await open_capture_event(
        db_session, org_id=org_id, operator_id=uuid.UUID(source["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", scanned_at=NOW,
    )
    await db_session.commit()
    event_id = event.id

    merged = await client.post(
        f"/operators/{source['id']}/merge", json={"target_operator_id": target["id"]}, headers=headers
    )
    assert merged.status_code == 200

    # The merge ran through the client fixture's own DB session (a different
    # session than db_session, same underlying sqlite connection) - expire
    # this session's identity map so the re-query below hits the DB instead
    # of returning the stale pre-merge object it already cached.
    db_session.expire_all()
    row = (await db_session.execute(select(CaptureEvent).where(CaptureEvent.id == event_id))).scalar_one()
    assert str(row.operator_id) == target["id"]

    listed = await client.get("/operators", headers=headers)
    ids = {o["id"] for o in listed.json()}
    assert source["id"] not in ids
    assert target["id"] in ids


async def test_merge_repoints_work_order_assignment_to_target(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    target = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    source = (await client.post("/operators", json={"name": "J. Kim"}, headers=headers)).json()

    wo = await _seed_work_order(db_session, org_id, uuid.UUID(source["id"]))
    wo_id = wo.id

    merged = await client.post(
        f"/operators/{source['id']}/merge", json={"target_operator_id": target["id"]}, headers=headers
    )
    assert merged.status_code == 200

    db_session.expire_all()
    row = (await db_session.execute(select(WorkOrder).where(WorkOrder.id == wo_id))).scalar_one()
    assert str(row.assigned_operator_id) == target["id"]


async def test_delete_operator_with_capture_event_is_blocked(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()

    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()
    event = await open_capture_event(
        db_session, org_id=org_id, operator_id=uuid.UUID(op["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", scanned_at=NOW,
    )
    await db_session.commit()
    event_id = event.id

    res = await client.delete(f"/operators/{op['id']}", headers=headers)
    assert res.status_code == 409

    assert (await db_session.execute(select(CaptureEvent).where(CaptureEvent.id == event_id))).scalar_one_or_none() is not None
    listed = await client.get("/operators", headers=headers)
    assert op["id"] in {o["id"] for o in listed.json()}


async def test_delete_operator_assigned_to_work_order_is_blocked(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()

    wo = await _seed_work_order(db_session, org_id, uuid.UUID(op["id"]))
    wo_id = wo.id

    res = await client.delete(f"/operators/{op['id']}", headers=headers)
    assert res.status_code == 409

    row = (await db_session.execute(select(WorkOrder).where(WorkOrder.id == wo_id))).scalar_one()
    assert str(row.assigned_operator_id) == op["id"]
    listed = await client.get("/operators", headers=headers)
    assert op["id"] in {o["id"] for o in listed.json()}


async def test_capture_events_route_scopes_by_org_and_days(client, db_session):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = await _org_id(client, headers)
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()

    recent = await open_capture_event(
        db_session, org_id=org_id, operator_id=uuid.UUID(op["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", scanned_at=NOW,
    )
    recent.ended_at = NOW
    # 20 days old rather than exactly 30: a boundary of exactly `days=30` would
    # be flaky, since the endpoint computes its own `since` a few
    # milliseconds after this NOW timestamp was captured, making the exact
    # 30-day-old row fall just outside the window depending on execution speed.
    old = CaptureEvent(
        org_id=org_id, operator_id=uuid.UUID(op["id"]), machine_id=uuid.UUID(machine["id"]),
        source="reader", started_at=NOW - timedelta(days=20),
        ended_at=NOW - timedelta(days=20) + timedelta(hours=1),
    )
    db_session.add(old)
    await db_session.commit()

    other_token = await _register_and_login(client, email="owner2@example.com", org_name="Other")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    res = await client.get("/operators/capture-events?days=7", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["operator_name"] == "J Kim"
    assert body[0]["machine_name"] == "Machine 1"
    assert body[0]["ended_at"] is not None

    res30 = await client.get("/operators/capture-events?days=30", headers=headers)
    assert len(res30.json()) == 2

    other_res = await client.get("/operators/capture-events", headers=other_headers)
    assert other_res.json() == []


async def test_capture_events_days_out_of_range_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/operators/capture-events?days=0", headers=headers)).status_code == 422
    assert (await client.get("/operators/capture-events?days=91", headers=headers)).status_code == 422
