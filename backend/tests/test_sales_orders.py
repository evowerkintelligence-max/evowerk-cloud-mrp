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


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_item(client, token, sku, name="Widget", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "finished_good",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_order(client, token, customer, item, qty=10, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


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


async def test_order_numbers_allocate_sequentially_per_org(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")

    numbers = [
        (await _create_order(client, token, customer, item))["so_number"] for _ in range(3)
    ]
    assert numbers == [1, 2, 3]


async def test_order_requires_at_least_one_line(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    res = await client.post(
        "/sales-orders",
        json={"customer_id": customer["id"], "needed_by": "2030-06-01", "lines": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_a_nonpositive_quantity(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "0", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_an_unknown_customer(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": "00000000-0000-0000-0000-000000000000",
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "5", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_cancelling_an_order_sets_cancelled(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item)

    res = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    again = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert again.status_code == 409


async def test_orders_are_org_scoped(client):
    token_a = await _register_and_login(client)
    customer = await _create_customer(client, token_a)
    item = await _create_item(client, token_a, "WIDGET-1")
    order = await _create_order(client, token_a, customer, item)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/sales-orders", headers=headers_b)).json() == []
    assert (await client.get(f"/sales-orders/{order['id']}", headers=headers_b)).status_code == 404
