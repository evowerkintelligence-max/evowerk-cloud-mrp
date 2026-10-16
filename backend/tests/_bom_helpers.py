from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD


PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod", item_type="raw_material", unit_of_measure="ea"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": item_type, "unit_of_measure": unit_of_measure},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()
