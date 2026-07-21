from __future__ import annotations

import pytest

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


async def test_schedule_crud(client):
    token = await _register_and_login(client)

    create = await client.post(
        "/predictive/schedules",
        headers=_h(token),
        json={"machine": "M2", "priority": "immediate", "action": "Replace hydraulic seal", "pm_hours": 16},
    )
    assert create.status_code == 201, create.text
    item = create.json()
    assert item["status"] == "pending"

    listed = await client.get("/predictive/schedules", headers=_h(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    patched = await client.patch(
        f"/predictive/schedules/{item['id']}", headers=_h(token), json={"status": "done"}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "done"

    deleted = await client.delete(f"/predictive/schedules/{item['id']}", headers=_h(token))
    assert deleted.status_code == 204

    listed_after = await client.get("/predictive/schedules", headers=_h(token))
    assert listed_after.json() == []


async def test_schedule_not_found(client):
    token = await _register_and_login(client)
    res = await client.patch(
        "/predictive/schedules/00000000-0000-0000-0000-000000000000", headers=_h(token), json={"status": "done"}
    )
    assert res.status_code == 404


async def test_predictive_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    await client.post(
        "/predictive/schedules", headers=_h(token_a), json={"machine": "M1", "action": "Inspect"}
    )
    res_b = await client.get("/predictive/schedules", headers=_h(token_b))
    assert res_b.json() == []
