# backend/tests/test_pace_tracking.py
from __future__ import annotations

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
