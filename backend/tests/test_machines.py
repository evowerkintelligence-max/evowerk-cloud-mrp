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


async def _create_work_center(client, token, code="CNC"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/work-centers", json={"code": code, "name": "CNC Milling"}, headers=headers)
    return res.json()


async def test_create_machine_under_work_center(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wc = await _create_work_center(client, token)
    res = await client.post(
        "/machines",
        json={"work_center_id": wc["id"], "code": "CNC-01", "name": "CNC 01", "mtbf_hours": 500, "mttr_hours": 2},
        headers=headers,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["work_center_id"] == wc["id"]
    assert body["needs_review"] is False


async def test_create_machine_unknown_work_center_404(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/machines",
        json={"work_center_id": "00000000-0000-0000-0000-000000000000", "code": "X", "name": "X"},
        headers=headers,
    )
    assert res.status_code == 404


async def test_filter_by_work_center_and_needs_review(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wc1 = await _create_work_center(client, token, "CNC")
    wc2 = await _create_work_center(client, token, "WELD")
    await client.post("/machines", json={"work_center_id": wc1["id"], "code": "CNC-01", "name": "A"}, headers=headers)
    m2 = (
        await client.post("/machines", json={"work_center_id": wc2["id"], "code": "WLD-01", "name": "B"}, headers=headers)
    ).json()
    await client.patch(f"/machines/{m2['id']}", json={"needs_review": True}, headers=headers)

    by_wc = await client.get(f"/machines?work_center_id={wc1['id']}", headers=headers)
    assert len(by_wc.json()) == 1

    flagged = await client.get("/machines?needs_review=true", headers=headers)
    assert len(flagged.json()) == 1
    assert flagged.json()[0]["code"] == "WLD-01"


async def test_duplicate_machine_code_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wc = await _create_work_center(client, token)
    await client.post("/machines", json={"work_center_id": wc["id"], "code": "CNC-01", "name": "A"}, headers=headers)
    dup = await client.post("/machines", json={"work_center_id": wc["id"], "code": "CNC-01", "name": "B"}, headers=headers)
    assert dup.status_code == 409


async def test_checkin_qr_svg(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wc = await _create_work_center(client, token)
    machine = (
        await client.post(
            "/machines", json={"work_center_id": wc["id"], "code": "M1", "name": "Machine 1"}, headers=headers
        )
    ).json()

    res = await client.get(f"/machines/{machine['id']}/checkin-qr", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in res.content


async def test_checkin_qr_unknown_machine_404(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(f"/machines/{uuid.uuid4()}/checkin-qr", headers=headers)
    assert res.status_code == 404
