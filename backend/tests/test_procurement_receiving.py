from __future__ import annotations

import pytest

from tests._procurement_helpers import (
    _register_and_login,
    _create_item,
    _create_supplier,
)

pytestmark = pytest.mark.asyncio


async def test_over_receipt_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    over = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "11"}],
        headers=headers,
    )
    assert over.status_code == 409


async def test_over_receipt_rejected_across_duplicate_lines_in_one_request(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    # Two receipts against the same line in one request, each individually
    # within bounds but exceeding quantity_ordered combined.
    over = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {"po_line_id": line_id, "quantity": "6"},
            {"po_line_id": line_id, "quantity": "6"},
        ],
        headers=headers,
    )
    assert over.status_code == 409

    # Nothing was applied - the line's quantity_received is untouched.
    detail = await client.get(f"/purchase-orders/{po_id}", headers=headers)
    assert detail.json()["lines"][0]["quantity_received"] == "0.0000"


async def test_receive_with_rejected_quantity_only_applies_accepted_to_inventory(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "10",
                "quantity_rejected": "3",
                "reject_reason": "damaged",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    # The full quantity (accepted + rejected) still closes out the line.
    assert receive.json()["lines"][0]["quantity_received"] == "10.0000"
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    # Only the accepted 7 units landed in inventory, not the full 10.
    assert inventory.json()["quantity_on_hand"] == "7.0000"


async def test_receive_fully_rejected_applies_no_inventory_transaction(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "5",
                "quantity_rejected": "5",
                "reject_reason": "wrong spec",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    assert inventory.json()["quantity_on_hand"] == "0.0000"


async def test_receive_rejects_quantity_rejected_exceeding_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "5", "quantity_rejected": "6"}],
        headers=headers,
    )
    assert receive.status_code == 400
