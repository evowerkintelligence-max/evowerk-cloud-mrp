from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _seed_machine_and_operator(client, token, badge_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    wc = (await client.post("/work-centers", json={"code": "WC1", "name": "Line 1"}, headers=headers)).json()
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()
    operator = (
        await client.post("/operators", json={"name": "J Kim", "badge_id": badge_id}, headers=headers)
    ).json()
    return machine, operator


async def test_badge_scan_creates_capture_event(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": machine["id"]})
    assert res.status_code == 201
    body = res.json()
    assert body["operator_id"] == operator["id"]
    assert body["operator_name"] == "J Kim"


async def test_badge_scan_unrecognized_badge_404(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "NOPE", "machine_id": machine["id"]})
    assert res.status_code == 404


async def test_badge_scan_unknown_machine_404(client):
    token = await _register_and_login(client)
    await _seed_machine_and_operator(client, token, badge_id="B1")

    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": str(uuid.uuid4())})
    assert res.status_code == 404


async def test_badge_id_scoped_per_org(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="Acme")
    machine_a, _ = await _seed_machine_and_operator(client, token_a, badge_id="B1")

    token_b = await _register_and_login(client, email="b@example.com", org_name="Zenith")
    await _seed_machine_and_operator(client, token_b, badge_id="B1")

    # B1 exists in both orgs (allowed - badge_id is unique per org, not
    # globally). Scanning it against org A's machine must resolve org A's
    # operator, never leak into org B's roster.
    res = await client.post("/operators/capture", json={"badge_id": "B1", "machine_id": machine_a["id"]})
    assert res.status_code == 201


async def test_checkin_roster_lists_operators(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token)

    res = await client.get(f"/checkin/{machine['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["machine_name"] == "Machine 1"
    assert [o["id"] for o in body["operators"]] == [operator["id"]]


async def test_checkin_unknown_machine_404(client):
    res = await client.get(f"/checkin/{uuid.uuid4()}")
    assert res.status_code == 404


async def test_checkin_with_existing_operator_id(client):
    token = await _register_and_login(client)
    machine, operator = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={"operator_id": operator["id"]})
    assert res.status_code == 201
    assert res.json()["operator_id"] == operator["id"]


async def test_checkin_with_new_name_creates_operator(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={"name": "New Hire"})
    assert res.status_code == 201
    assert res.json()["operator_name"] == "New Hire"

    roster = await client.get(f"/checkin/{machine['id']}")
    assert "New Hire" in [o["name"] for o in roster.json()["operators"]]


async def test_checkin_requires_operator_id_or_name(client):
    token = await _register_and_login(client)
    machine, _ = await _seed_machine_and_operator(client, token)

    res = await client.post(f"/checkin/{machine['id']}", json={})
    assert res.status_code == 400
