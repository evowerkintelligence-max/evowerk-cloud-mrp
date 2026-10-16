from __future__ import annotations


PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name, "email": "sales@acmemetals.example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()
