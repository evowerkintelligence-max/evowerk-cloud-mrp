from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _create_po(client, token, supplier_id):
    res = await client.post(
        "/purchase-orders",
        json={"supplier_id": supplier_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def test_list_purchase_orders_search_by_supplier_and_po_number(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    alpha = await _create_supplier(client, token, name="Alpha Supply")
    beta = await _create_supplier(client, token, name="Beta Supply")
    po_alpha = await _create_po(client, token, alpha["id"])
    await _create_po(client, token, beta["id"])

    by_supplier = await client.get("/purchase-orders?q=alpha", headers=headers)
    assert [po["id"] for po in by_supplier.json()] == [po_alpha["id"]]

    by_number = await client.get(f"/purchase-orders?q={po_alpha['po_number']}", headers=headers)
    assert po_alpha["id"] in {po["id"] for po in by_number.json()}


async def test_list_purchase_orders_pagination(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    for _ in range(3):
        await _create_po(client, token, supplier["id"])

    page1 = await client.get("/purchase-orders?limit=2&offset=0", headers=headers)
    page2 = await client.get("/purchase-orders?limit=2&offset=2", headers=headers)
    assert len(page1.json()) == 2
    assert len(page2.json()) == 1
    assert {po["id"] for po in page1.json()} & {po["id"] for po in page2.json()} == set()


async def test_submit_locks_po_and_second_submit_is_rejected(client):
    # Not a true concurrency test (the test client is sequential), but proves
    # the for_update-locked read-then-write path still leaves the PO in a
    # single consistent state: a second submit against an already-submitted
    # PO must fail rather than silently succeed twice.
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    po = await _create_po(client, token, supplier["id"])
    item_res = await client.post(
        "/items",
        json={"sku": "ROD", "name": "Rod", "item_type": "component", "unit_of_measure": "ea"},
        headers=headers,
    )
    item = item_res.json()
    await client.patch(
        f"/purchase-orders/{po['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )

    first = await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "submitted"

    second = await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
    assert second.status_code == 409
