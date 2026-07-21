from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _setup(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    item = (
        await client.post("/items", json={"sku": "FG-1", "name": "Widget", "item_type": "finished_good"}, headers=headers)
    ).json()
    wc = (await client.post("/work-centers", json={"code": "CUT", "name": "Cutting"}, headers=headers)).json()
    return item, wc, headers


async def test_create_routing_server_computed_version(client):
    token = await _register_and_login(client)
    item, wc, headers = await _setup(client, token)
    r1 = await client.post(f"/routings?item_id={item['id']}", headers=headers)
    assert r1.status_code == 201
    assert r1.json()["version"] == 1
    assert r1.json()["status"] == "draft"

    r2 = await client.post(f"/routings?item_id={item['id']}", headers=headers)
    assert r2.json()["version"] == 2


async def test_add_steps_and_activate(client):
    token = await _register_and_login(client)
    item, wc, headers = await _setup(client, token)
    routing = (await client.post(f"/routings?item_id={item['id']}", headers=headers)).json()

    steps_res = await client.post(
        f"/routings/{routing['id']}/steps",
        json=[{"sequence": 1, "work_center_id": wc["id"], "cycle_time_seconds": 30.0}],
        headers=headers,
    )
    assert steps_res.status_code == 200
    assert len(steps_res.json()["steps"]) == 1

    activate = await client.post(f"/routings/{routing['id']}/activate", headers=headers)
    assert activate.status_code == 200
    assert activate.json()["status"] == "active"


async def test_activate_without_steps_rejected(client):
    token = await _register_and_login(client)
    item, wc, headers = await _setup(client, token)
    routing = (await client.post(f"/routings?item_id={item['id']}", headers=headers)).json()
    res = await client.post(f"/routings/{routing['id']}/activate", headers=headers)
    assert res.status_code == 400


async def test_activating_new_version_archives_old(client):
    token = await _register_and_login(client)
    item, wc, headers = await _setup(client, token)

    r1 = (await client.post(f"/routings?item_id={item['id']}", headers=headers)).json()
    await client.post(
        f"/routings/{r1['id']}/steps",
        json=[{"sequence": 1, "work_center_id": wc["id"]}], headers=headers,
    )
    await client.post(f"/routings/{r1['id']}/activate", headers=headers)

    r2 = (await client.post(f"/routings?item_id={item['id']}", headers=headers)).json()
    await client.post(
        f"/routings/{r2['id']}/steps",
        json=[{"sequence": 1, "work_center_id": wc["id"]}], headers=headers,
    )
    await client.post(f"/routings/{r2['id']}/activate", headers=headers)

    listed = (await client.get(f"/routings?item_id={item['id']}", headers=headers)).json()
    statuses = {r["version"]: r["status"] for r in listed}
    assert statuses == {1: "archived", 2: "active"}
