from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

from decimal import Decimal

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


async def _create_item(client, token, sku, name="Item", min_order_qty=None):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": str(qty), "sequence": i}
                for i, (comp, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post("/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    return res.json()


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def test_build_plan_no_yield_loss(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG", "Widget")
    rod = await _create_item(client, token, "BLD-ROD", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])

    res = await client.post(
        "/mrp/build-plan", json={"item_id": fg["id"], "build_quantity": "100"}, headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["item_sku"] == "BLD-FG"
    lines_by_sku = {line["item_sku"]: line for line in body["lines"]}
    assert _dec(lines_by_sku["BLD-FG"]["net_requirement"]) == Decimal("100")
    assert _dec(lines_by_sku["BLD-ROD"]["net_requirement"]) == Decimal("200")


async def test_build_plan_applies_yield_loss(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG2", "Widget")
    rod = await _create_item(client, token, "BLD-ROD2", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 1)])

    # Need 95 good units, expect 5% of attempts to fail -> attempt 95/0.95 = 100.
    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg["id"], "build_quantity": "95", "yield_loss_pct": "5"},
        headers=headers,
    )
    assert res.status_code == 200
    lines_by_sku = {line["item_sku"]: line for line in res.json()["lines"]}
    assert _dec(lines_by_sku["BLD-FG2"]["net_requirement"]) == Decimal("100")
    assert _dec(lines_by_sku["BLD-ROD2"]["net_requirement"]) == Decimal("100")


async def test_build_plan_rejects_yield_loss_of_100(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG3", "Widget")
    rod = await _create_item(client, token, "BLD-ROD3", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 1)])

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg["id"], "build_quantity": "10", "yield_loss_pct": "100"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_build_plan_rejects_item_with_no_active_bom(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    raw = await _create_item(client, token, "BLD-RAW", "Raw Material")

    res = await client.post(
        "/mrp/build-plan", json={"item_id": raw["id"], "build_quantity": "10"}, headers=headers
    )
    assert res.status_code == 400


async def test_build_plan_nets_against_on_hand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG4", "Widget")
    rod = await _create_item(client, token, "BLD-ROD4", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    plines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": rod["id"], "quantity_ordered": "50", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    assert plines.status_code == 200
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    res = await client.post(
        "/mrp/build-plan", json={"item_id": fg["id"], "build_quantity": "100"}, headers=headers
    )
    lines_by_sku = {line["item_sku"]: line for line in res.json()["lines"]}
    # 200 rods needed, 50 already on order -> net 150.
    assert _dec(lines_by_sku["BLD-ROD4"]["on_order"]) == Decimal("50")
    assert _dec(lines_by_sku["BLD-ROD4"]["net_requirement"]) == Decimal("150")


async def test_generate_pos_from_build_plan_groups_by_preferred_supplier(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG5", "Widget")
    rod = await _create_item(client, token, "BLD-ROD5", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])
    supplier = await _create_supplier(client, token)

    set_supplier = await client.patch(
        f"/items/{rod['id']}", json={"preferred_supplier_id": supplier["id"]}, headers=headers
    )
    assert set_supplier.status_code == 200

    res = await client.post(
        "/mrp/build-plan/generate-pos",
        json={"item_id": fg["id"], "build_quantity": "100"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["line_count"] == 1
    assert body["unassigned"] == []

    pos = await client.get("/purchase-orders", headers=headers)
    assert any(po["po_number"] == body["created"][0]["po_number"] for po in pos.json())


async def test_generate_pos_from_build_plan_reports_unassigned(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "BLD-FG6", "Widget")
    rod = await _create_item(client, token, "BLD-ROD6", "Steel Rod")  # no preferred supplier
    await _create_active_bom(client, token, fg, [(rod, 2)])

    res = await client.post(
        "/mrp/build-plan/generate-pos",
        json={"item_id": fg["id"], "build_quantity": "100"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == []
    assert len(body["unassigned"]) == 1
    assert body["unassigned"][0]["item_sku"] == "BLD-ROD6"


async def test_build_plan_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    fg_a = await _create_item(client, token_a, "BLD-ISO", "Widget")

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": fg_a["id"], "build_quantity": "10"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    # Org B can't see org A's item at all.
    assert res.status_code == 404
