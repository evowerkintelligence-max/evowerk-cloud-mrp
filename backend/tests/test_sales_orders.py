from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def test_new_org_defaults_to_consuming_demand(client):
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["demand_policy"] == "consume"


async def test_demand_policy_can_be_set_to_add(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.patch("/orgs/me", json={"demand_policy": "add"}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["demand_policy"] == "add"
    assert (await client.get("/orgs/me", headers=headers)).json()["demand_policy"] == "add"


async def test_unknown_demand_policy_is_rejected(client):
    token = await _register_and_login(client)
    res = await client.patch(
        "/orgs/me", json={"demand_policy": "whatever"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400
