from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _create_lot_tracked_item(client, token, sku="LOT-1", name="Chemical"):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "raw_material",
            "unit_of_measure": "ea",
            "is_lot_tracked": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _receive_lot(client, token, item, supplier, qty, lot_number, expiry_date=None):
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    payload = {"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}
    if expiry_date is not None:
        payload["expiry_date"] = expiry_date.isoformat()
    recv = await client.post(f"/purchase-orders/{po_id}/receive", json=[payload], headers=headers)
    assert recv.status_code == 200

    lots = await client.get(f"/lots?item_id={item['id']}", headers=headers)
    return next(lot for lot in lots.json() if lot["lot_number"] == lot_number)


async def test_lot_alert_on_hold_ranked_before_expiring_soon(client):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    # Expires tomorrow but still active - would sort first on expiry
    # proximity alone. A held lot must still outrank it: it already needs a
    # human decision, an expiring-but-unheld lot doesn't yet.
    expiring_item = await _create_lot_tracked_item(client, token, "EXP-1")
    await _receive_lot(
        client, token, expiring_item, supplier, 10, "LOT-EXP",
        expiry_date=date.today() + timedelta(days=1),
    )

    held_item = await _create_lot_tracked_item(client, token, "HOLD-1")
    held_lot = await _receive_lot(
        client, token, held_item, supplier, 10, "LOT-HOLD",
        expiry_date=date.today() + timedelta(days=365),
    )
    assert (await client.post(f"/lots/{held_lot['id']}/hold", headers=headers)).status_code == 200

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    alerts = res.json()["lot_alerts"]
    assert [a["item_sku"] for a in alerts] == ["HOLD-1", "EXP-1"]
    assert alerts[0]["reason"] == "on_hold"
    assert alerts[1]["reason"] == "expiring_soon"


async def test_lot_alert_excludes_depleted_and_distant_expiry(client):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    depleted_item = await _create_lot_tracked_item(client, token, "DEP-1")
    depleted_lot = await _receive_lot(client, token, depleted_item, supplier, 10, "LOT-DEP")
    assert (
        await client.post(f"/lots/{depleted_lot['id']}/consume", json={"quantity": "10"}, headers=headers)
    ).status_code == 200

    # Active, but expiry is far outside the warning window - not actionable.
    distant_item = await _create_lot_tracked_item(client, token, "FAR-1")
    await _receive_lot(
        client, token, distant_item, supplier, 10, "LOT-FAR",
        expiry_date=date.today() + timedelta(days=365),
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    assert res.json()["lot_alerts"] == []


async def test_lot_alert_org_isolation(client):
    reg_a, token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    supplier_a = await _create_supplier(client, token_a)
    item_a = await _create_lot_tracked_item(client, token_a, "A-ITEM")
    await _receive_lot(
        client, token_a, item_a, supplier_a, 10, "LOT-A",
        expiry_date=date.today() + timedelta(days=1),
    )

    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")
    res = await client.get("/inventory/morning-brief", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["lot_alerts"] == []
