from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit
HORIZON = "?horizon_start=2030-01-01&horizon_end=2030-12-31"


def _dec(v) -> Decimal:
    # Numeric(12, 4) values serialize scaled when DB-round-tripped, bare when
    # computed in Python - compare the number, not its string form.
    return Decimal(str(v))


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_item(
    client,
    token,
    sku,
    name="Item",
    is_lot_tracked=False,
    lead_time_days=None,
    min_order_qty=None,
    safety_stock_qty=None,
):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
            "lead_time_days": lead_time_days,
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
            "safety_stock_qty": str(safety_stock_qty) if safety_stock_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per) or (component_item, qty_per,
    scrap_pct). Creates a draft BOM, sets its lines, and activates it."""
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    bom_id = bom_data["id"]
    patch = await client.patch(
        f"/boms/{bom_id}",
        json={
            "lines": [
                {
                    "component_item_id": line[0]["id"],
                    "quantity": str(line[1]),
                    "sequence": i,
                    "scrap_pct": str(line[2]) if len(line) > 2 else "0",
                }
                for i, line in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200
    activate = await client.post(f"/boms/{bom_id}/activate", headers=headers)
    assert activate.status_code == 200
    return bom_id


async def _forecast(client, token, item, qty, period="2030-06-01"):
    res = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": period, "quantity": str(qty)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _receive_into_lot(client, token, item, supplier, qty, lot_number):
    """Gives an item real on-hand stock via a received PO lot (item must be
    lot-tracked)."""
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
    recv = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}],
        headers=headers,
    )
    assert recv.status_code == 200


def _by_sku(plan_json) -> dict:
    return {line["item_sku"]: line for line in plan_json["lines"]}


async def test_single_level_explosion(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    bolt = await _create_item(client, token, "BOLT", "Bolt")
    await _create_active_bom(client, token, fg, [(rod, 2), (bolt, 4)])

    await _forecast(client, token, fg, 100)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    assert plan.status_code == 200
    lines = _by_sku(plan.json())
    assert set(lines) == {"FG", "ROD", "BOLT"}

    # Finished good: independent demand, manufactured.
    assert lines["FG"]["is_manufactured"] is True
    assert _dec(lines["FG"]["independent_demand"]) == Decimal("100")
    assert _dec(lines["FG"]["dependent_demand"]) == Decimal("0")
    assert _dec(lines["FG"]["net_requirement"]) == Decimal("100")

    # Components: purely dependent demand, purchased (not manufactured).
    assert lines["ROD"]["is_manufactured"] is False
    assert _dec(lines["ROD"]["independent_demand"]) == Decimal("0")
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("200")  # 100 x 2
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("200")
    assert _dec(lines["BOLT"]["dependent_demand"]) == Decimal("400")  # 100 x 4
    assert _dec(lines["BOLT"]["net_requirement"]) == Decimal("400")


async def test_netted_explosion_finished_good_stock_reduces_component_need(client):
    """The heart of netted (vs gross) MRP: finished-good stock must cut down
    component requirements, not just the finished good's own net."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget", is_lot_tracked=True)
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _create_active_bom(client, token, fg, [(rod, 2)])

    await _forecast(client, token, fg, 100)
    # 30 finished goods already on hand -> only 70 need building.
    await _receive_into_lot(client, token, fg, supplier, 30, "FG-LOT-1")

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["FG"]["on_hand"]) == Decimal("30")
    assert _dec(lines["FG"]["net_requirement"]) == Decimal("70")
    # Component demand explodes from the NET 70, not the gross 100: 70 x 2 = 140.
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("140")
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("140")


async def test_finished_good_fully_covered_drops_components(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget", is_lot_tracked=True)
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _create_active_bom(client, token, fg, [(rod, 2)])

    await _forecast(client, token, fg, 50)
    await _receive_into_lot(client, token, fg, supplier, 50, "FG-LOT-1")  # fully covered

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["FG"]["net_requirement"]) == Decimal("0")
    # Net requirement is 0, so nothing explodes to the component - it isn't a
    # planning action and is omitted entirely.
    assert "ROD" not in lines


async def test_multi_level_explosion(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    sub = await _create_item(client, token, "SUB", "Subassembly")
    raw = await _create_item(client, token, "RAW", "Raw Bar")
    # FG -> 1x SUB -> 3x RAW
    await _create_active_bom(client, token, fg, [(sub, 1)])
    await _create_active_bom(client, token, sub, [(raw, 3)])

    await _forecast(client, token, fg, 100)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["FG"]["net_requirement"]) == Decimal("100")
    assert lines["SUB"]["is_manufactured"] is True
    assert _dec(lines["SUB"]["dependent_demand"]) == Decimal("100")  # 100 x 1
    assert _dec(lines["SUB"]["net_requirement"]) == Decimal("100")
    assert lines["RAW"]["is_manufactured"] is False
    assert _dec(lines["RAW"]["dependent_demand"]) == Decimal("300")  # 100 x 3
    assert _dec(lines["RAW"]["net_requirement"]) == Decimal("300")


async def test_shared_component_demand_sums_across_parents(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg1 = await _create_item(client, token, "FG1", "Widget A")
    fg2 = await _create_item(client, token, "FG2", "Widget B")
    bolt = await _create_item(client, token, "BOLT", "Bolt")
    await _create_active_bom(client, token, fg1, [(bolt, 2)])
    await _create_active_bom(client, token, fg2, [(bolt, 3)])

    await _forecast(client, token, fg1, 10)
    await _forecast(client, token, fg2, 10)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    # Shared component sums both parents' explosions: 10x2 + 10x3 = 50.
    assert _dec(lines["BOLT"]["dependent_demand"]) == Decimal("50")
    assert _dec(lines["BOLT"]["net_requirement"]) == Decimal("50")


async def test_independent_and_dependent_demand_combine(client):
    """An item can be both sold directly (independent) and consumed by an
    assembly (dependent) - the plan sums both into gross demand."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 2)])

    await _forecast(client, token, fg, 100)  # -> 200 dependent rod demand
    await _forecast(client, token, rod, 50)  # 50 independent rod demand (sold as spare)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["ROD"]["independent_demand"]) == Decimal("50")
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("200")
    assert _dec(lines["ROD"]["gross_demand"]) == Decimal("250")
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("250")


async def test_component_on_order_nets_after_explosion(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _create_active_bom(client, token, fg, [(rod, 2)])

    await _forecast(client, token, fg, 100)  # -> 200 rod required

    # 60 rods already on order (submitted, unreceived) -> rod net = 200 - 60 = 140.
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    plines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": rod["id"], "quantity_ordered": "60", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    assert plines.status_code == 200
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("200")
    assert _dec(lines["ROD"]["on_order"]) == Decimal("60")
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("140")


async def test_only_active_bom_explodes(client):
    """A draft BOM must not drive planning - only the active one does."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")

    # Draft BOM only (created + lines set, never activated).
    bom = await client.post("/boms", json={"item_id": fg["id"]}, headers=headers)
    bom_data = bom.json()
    patch = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": rod["id"], "quantity": "2", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200

    await _forecast(client, token, fg, 100)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    # FG plans as a plain (unexploded) item; no component demand appears.
    assert lines["FG"]["is_manufactured"] is False
    assert "ROD" not in lines


async def test_lead_time_offsets_suggested_order_date(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    rod = await _create_item(client, token, "ROD", "Steel Rod", lead_time_days=10)
    await _forecast(client, token, rod, 50, period="2030-06-15")

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert lines["ROD"]["need_by_date"] == "2030-06-15"
    # 2030-06-15 minus 10 days.
    assert lines["ROD"]["suggested_order_date"] == "2030-06-05"


async def test_no_lead_time_leaves_order_date_unset(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    rod = await _create_item(client, token, "ROD", "Steel Rod")  # no lead_time_days
    await _forecast(client, token, rod, 50, period="2030-06-15")

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert lines["ROD"]["need_by_date"] == "2030-06-15"
    assert lines["ROD"]["suggested_order_date"] is None


async def test_lead_time_need_date_propagates_through_explosion(client):
    """A component's need date comes from its parent's need date, not its own
    (a pure component has no direct forecast to date it by)."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod", lead_time_days=7)
    await _create_active_bom(client, token, fg, [(rod, 2)])
    await _forecast(client, token, fg, 100, period="2030-03-01")

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert lines["ROD"]["need_by_date"] == "2030-03-01"
    assert lines["ROD"]["suggested_order_date"] == "2030-02-22"  # 2030-03-01 minus 7 days


async def test_moq_rounds_up_suggested_quantity_without_inflating_explosion(client):
    """MOQ rounds the item's own order quantity but must not leak into what
    its components need - explosion always uses the raw net."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod", min_order_qty=500)
    await _create_active_bom(client, token, fg, [(rod, 2)])
    await _forecast(client, token, fg, 10)  # -> 20 rods needed, below the 500 MOQ

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("20")
    assert _dec(lines["ROD"]["suggested_order_quantity"]) == Decimal("500")


async def test_moq_below_net_requirement_is_a_no_op(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    rod = await _create_item(client, token, "ROD", "Steel Rod", min_order_qty=10)
    await _forecast(client, token, rod, 200)  # already well above the MOQ

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("200")
    assert _dec(lines["ROD"]["suggested_order_quantity"]) == Decimal("200")


async def test_scrap_pct_inflates_component_explosion(client):
    """A component's user-set scrap % (failure leeway) is extra material
    ordered on top of the raw BOM quantity, not a separate planning step."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    # 2 rods per FG, plus 10% expected scrap -> 2.2 rods/FG.
    await _create_active_bom(client, token, fg, [(rod, 2, 10)])

    await _forecast(client, token, fg, 100)  # -> 220 rods required, not 200

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("220")
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("220")


async def test_manufactured_item_ignores_lead_time_and_moq(client):
    """MOQ/lead time are purchasing concepts - a build action never rounds or
    offsets by them, even if the fields happen to be set on the item."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget", lead_time_days=5, min_order_qty=1000)
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    await _create_active_bom(client, token, fg, [(rod, 1)])
    await _forecast(client, token, fg, 10, period="2030-06-15")

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert lines["FG"]["is_manufactured"] is True
    assert _dec(lines["FG"]["suggested_order_quantity"]) == Decimal("10")  # not rounded to 1000
    assert lines["FG"]["suggested_order_date"] is None  # build, not buy - no order date


async def test_safety_stock_inflates_net_requirement(client):
    """Safety stock is demand on top of the forecast: 5 forecast + a 10 buffer
    with nothing on hand or on order = buy 15, not 5. An item that leaves the
    field unset must be untouched by the feature."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    buffered = await _create_item(client, token, "BUF", "Buffered Part", safety_stock_qty=10)
    plain = await _create_item(client, token, "PLAIN", "Unbuffered Part")
    await _forecast(client, token, buffered, 5)
    await _forecast(client, token, plain, 5)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["BUF"]["gross_demand"]) == Decimal("5")
    assert _dec(lines["BUF"]["on_hand"]) == Decimal("0")
    assert _dec(lines["BUF"]["on_order"]) == Decimal("0")
    assert _dec(lines["BUF"]["net_requirement"]) == Decimal("15")
    assert _dec(lines["BUF"]["suggested_order_quantity"]) == Decimal("15")
    # safety_stock_qty unset -> exactly the pre-feature numbers.
    assert _dec(lines["PLAIN"]["net_requirement"]) == Decimal("5")


async def test_safety_stock_on_assembly_explodes_to_components(client):
    """A buffered assembly's inflated net is its components' gross - you can't
    hold a buffer of finished goods without the parts to build it. Components
    stack their own buffer on top."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    fg = await _create_item(client, token, "FG", "Widget", safety_stock_qty=10)
    rod = await _create_item(client, token, "ROD", "Steel Rod", safety_stock_qty=7)
    await _create_active_bom(client, token, fg, [(rod, 2)])
    await _forecast(client, token, fg, 100)

    plan = await client.get(f"/mrp/plan{HORIZON}", headers=headers)
    lines = _by_sku(plan.json())
    assert _dec(lines["FG"]["net_requirement"]) == Decimal("110")
    assert _dec(lines["ROD"]["dependent_demand"]) == Decimal("220")  # 110 x 2
    assert _dec(lines["ROD"]["net_requirement"]) == Decimal("227")  # + its own buffer
