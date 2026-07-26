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


async def _create_item(client, token, sku, name="Item", lead_time_days=None, min_order_qty=None):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "is_lot_tracked": False,
            "lead_time_days": lead_time_days,
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per)."""
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    patch = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": component["id"],
                    "quantity": str(qty),
                    "sequence": i,
                    "scrap_pct": "0",
                }
                for i, (component, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200
    return bom_data["id"]


async def _plan(client, token, item, qty, needed_by=None):
    """Set the grid's planned quantity for an item."""
    body = {"quantity": str(qty)}
    if needed_by is not None:
        body["needed_by"] = needed_by
    res = await client.put(
        f"/planning/grid/{item['id']}", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _grid(client, token):
    res = await client.get("/planning/grid", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _product(grid, sku):
    for row in grid["products"]:
        if row["item_sku"] == sku:
            return row
    raise AssertionError(f"{sku} not in grid: {[r['item_sku'] for r in grid['products']]}")


async def test_only_top_level_products_get_rows(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    sub = await _create_item(client, token, "SUB-1")
    pump = await _create_item(client, token, "PUMP-A2", name="Coolant pump")
    await _create_active_bom(client, token, sub, [(seal, 2)])
    await _create_active_bom(client, token, pump, [(sub, 1)])

    grid = await _grid(client, token)

    skus = {row["item_sku"] for row in grid["products"]}
    assert skus == {"PUMP-A2"}, "sub-assemblies and raw components must not get their own rows"
    assert _product(grid, "PUMP-A2")["item_name"] == "Coolant pump"


async def test_forecasted_item_without_bom_gets_a_row(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["planned_qty"] == "10.0000"


async def test_missing_when_a_component_is_short(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["SEAL-V2"]
    assert grid["summary"]["missing"] == 1


async def test_in_stock_when_components_are_covered(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers=headers,
    )
    assert adj.status_code == 200, adj.text
    # An owner never queues for approval, so the stock is on the shelf now. If
    # this ever fails, the seeding is the problem, not the grid.
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "in_stock"
    assert row["missing_materials"] == []
    assert grid["summary"]["in_stock"] == 1


async def test_arriving_when_a_component_is_only_covered_by_an_open_po(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post("/suppliers", json={"name": "Sealtech"}, headers=headers)
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
    po_id = po.json()["id"]
    await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": seal["id"], "quantity_ordered": "500", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    assert submit.status_code == 200, submit.text

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "arriving"
    assert row["missing_materials"] == []
    assert grid["summary"]["arriving"] == 1


async def test_purchased_product_judges_itself(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["materials_status"] == "missing"
    assert row["missing_materials"] == ["WIDGET-1"]


async def test_missing_rows_sort_above_covered_rows(client):
    token = await _register_and_login(client)
    covered = await _create_item(client, token, "AAA-COVERED")
    short = await _create_item(client, token, "ZZZ-SHORT")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"/inventory/{covered['id']}/adjust",
        json={"quantity": "100", "reason_code": "found_stock"},
        headers=headers,
    )

    await _plan(client, token, covered, 5)
    await _plan(client, token, short, 5)
    grid = await _grid(client, token)

    assert [row["item_sku"] for row in grid["products"]] == ["ZZZ-SHORT", "AAA-COVERED"]


async def test_grid_is_org_scoped(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    await _plan(client, token_a, widget, 10)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    grid_b = await _grid(client, token_b)

    assert grid_b["products"] == []
