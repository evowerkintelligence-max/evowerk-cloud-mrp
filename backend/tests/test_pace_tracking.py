# backend/tests/test_pace_tracking.py
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email: str, org_name: str = "PaceOrg") -> str:
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _start_session(client, headers, line: str = "Line 1", operator: str = "J. Alvarez") -> str:
    res = await client.post(
        "/pace/sessions", headers=headers, json={"line": line, "operator": operator, "target_rate": None}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_pace_full_lifecycle_no_pauses(client):
    token = await _register_and_login(client, "pacelife@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    session_id = await _start_session(client, headers)

    for _ in range(5):
        res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
        assert res.status_code == 200
    for _ in range(2):
        res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject"})
        assert res.status_code == 200

    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "stopped"
    assert body["good_count"] == 5
    assert body["reject_count"] == 2
    assert body["dataset_id"]


async def test_pace_reject_reason_captured_and_tallied(client):
    token = await _register_and_login(client, "pacereason@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    res = await client.post(
        f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject", "reason": "dimensional"}
    )
    assert res.status_code == 200
    assert res.json()["reject_reasons"] == {"dimensional": 1}

    res = await client.post(
        f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject", "reason": "dimensional"}
    )
    assert res.json()["reject_reasons"] == {"dimensional": 2}

    # No reason supplied -> bucketed as "Unspecified", not dropped.
    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject"})
    assert res.json()["reject_reasons"] == {"dimensional": 2, "Unspecified": 1}

    # A reason on a "good" tap is ignored - reason only applies to rejects.
    res = await client.post(
        f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good", "reason": "dimensional"}
    )
    body = res.json()
    assert body["good_count"] == 1
    assert body["reject_reasons"] == {"dimensional": 2, "Unspecified": 1}


async def test_pace_state_machine_guards(client):
    token = await _register_and_login(client, "paceguard@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    assert res.status_code == 409

    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert res.status_code == 200

    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)
    assert res.status_code == 409
    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert res.status_code == 409


async def test_pace_multi_cycle_pause_sums_not_overwrites(client):
    token = await _register_and_login(client, "pacecycle@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.3)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.1)
    resumed = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    unplanned = resumed.json()["unplanned_dt_min"]
    expected_min = (0.3 + 0.1) / 60
    # Both windows counted (not just the last ~0.1s one) - proves the SQL
    # increment sums across pause/resume cycles instead of overwriting.
    assert unplanned > expected_min * 0.5
    assert unplanned < expected_min * 3


async def test_pace_stop_while_paused_counts_open_pause_window(client):
    token = await _register_and_login(client, "pacestopwhile@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.2)
    # No resume call - stop must finalize the still-open pause window itself.
    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)

    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "stopped"
    assert body["planned_dt_min"] > 0


async def test_pace_mixed_pause_reasons_tracked_separately(client):
    token = await _register_and_login(client, "pacemixed@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.15)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.1)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "other"})
    await asyncio.sleep(0.1)
    resumed = await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    body = resumed.json()
    assert body["unplanned_dt_min"] > 0
    assert body["planned_dt_min"] > 0
    assert body["other_dt_min"] > 0


async def test_pace_shift_handoff_allows_other_member_to_act(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "paceowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_id = await _start_session(client, headers, line="Line A", operator="Alice")

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="paceoperatorb@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "paceoperatorb@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Operator B (a different org member) can tap, pause, resume, and stop
    # Operator A's still-running session - the deliberate shift-handoff allowance.
    res = await client.post(f"/pace/sessions/{session_id}/tap", headers=member_headers, json={"kind": "good"})
    assert res.status_code == 200
    res = await client.post(
        f"/pace/sessions/{session_id}/pause", headers=member_headers, json={"reason": "breakdown"}
    )
    assert res.status_code == 200
    res = await client.post(f"/pace/sessions/{session_id}/resume", headers=member_headers)
    assert res.status_code == 200
    res = await client.post(f"/pace/sessions/{session_id}/stop", headers=member_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "stopped"


async def test_pace_discard_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "pdowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_id = await _start_session(client, headers)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="pdmember@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "pdmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # a different member can't discard someone else's session...
    res = await client.delete(f"/pace/sessions/{session_id}", headers=member_headers)
    assert res.status_code == 403

    # ...but the creator can discard their own, in any status (still "active" here).
    res = await client.delete(f"/pace/sessions/{session_id}", headers=headers)
    assert res.status_code == 204


async def test_pace_discard_allowed_for_owner_deleting_someone_elses_session(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.security import hash_password
    from app.models.user import User
    import uuid as uuid_module

    token = await _register_and_login(client, "pd2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db_session:
        db_session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="pd2member@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await db_session.commit()

    login = await client.post("/auth/login", json={"email": "pd2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    session_id = await _start_session(client, member_headers)

    # the org owner can discard a different member's session.
    res = await client.delete(f"/pace/sessions/{session_id}", headers=headers)
    assert res.status_code == 204


async def test_pace_materialized_dataset_feeds_oee_with_unclassified_downtime(client):
    token = await _register_and_login(client, "paceoee@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = await _start_session(client, headers, line="Line 7", operator="R. Chen")

    for _ in range(10):
        await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"})
    for _ in range(2):
        await client.post(f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "reject"})

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "breakdown"})
    await asyncio.sleep(0.15)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "setup"})
    await asyncio.sleep(0.1)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    # >3s pushes the "other" pause past _six_big_losses' 0.05-minute floor for
    # reporting the resulting gap as its own "Unclassified Downtime" bucket.
    await client.post(f"/pace/sessions/{session_id}/pause", headers=headers, json={"reason": "other"})
    await asyncio.sleep(3.2)
    await client.post(f"/pace/sessions/{session_id}/resume", headers=headers)

    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    body = stopped.json()
    dataset_id = body["dataset_id"]
    assert body["good_count"] == 10
    assert body["reject_count"] == 2

    oee_res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "unplanned_downtime_col": "unplanned_dt_min",
            "planned_downtime_col": "planned_dt_min",
        },
    )
    assert oee_res.status_code == 200
    oee = oee_res.json()

    assert 0 <= oee["availability"] <= 100
    assert 0 <= oee["quality"] <= 100
    assert oee["total_units"] == 12
    assert oee["good_units"] == 10
    assert oee["quality"] == round(10 / 12 * 100, 2)

    six = oee["six_big_losses"]
    assert six is not None
    assert six["availability_split"] is True
    buckets = {b["key"]: b for b in six["buckets"]}
    assert "equipment_failure" in buckets  # breakdown pause -> unplanned_dt_min column
    assert "setup_adjustments" in buckets  # setup pause -> planned_dt_min column
    # "other" isn't its own CSV column, so its time falls into the leftover
    # gap between planned_min and (operating_min + unplanned + planned) -
    # exactly what _six_big_losses buckets as Unclassified Downtime.
    assert "unclassified_downtime" in buckets
    assert buckets["unclassified_downtime"]["minutes"] > 0
