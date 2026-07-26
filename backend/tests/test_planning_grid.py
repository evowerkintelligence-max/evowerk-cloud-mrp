from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.user import User
from app.services.planning_grid import DEFAULT_NEEDED_BY_DAYS, GRID_HORIZON_DAYS

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

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "10.0000"
    assert Decimal(row["on_hand"]) == Decimal(0)
    today = datetime.now(timezone.utc).date()
    assert row["needed_by"] == (today + timedelta(days=DEFAULT_NEEDED_BY_DAYS)).isoformat()


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


async def test_sub_assembly_shortfall_does_not_turn_the_product_red(client):
    """A sub-assembly's own net_requirement is a build instruction, not a
    material to order. PUMP-A2 has no stock and neither does SUB-1, but every
    buyable part (SEAL-V2) is on the shelf, so the row must be in_stock, not
    missing with SUB-1 listed."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    sub = await _create_item(client, token, "SUB-1", lead_time_days=45)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, sub, [(seal, 2)])
    await _create_active_bom(client, token, pump, [(sub, 1)])
    headers = {"Authorization": f"Bearer {token}"}
    # PUMP-A2 planned at 50 with no stock on either PUMP-A2 or SUB-1 nets a
    # build requirement of 50 SUB-1, which explodes to 100 SEAL-V2 (qty_per 2).
    # Stock SEAL-V2 well above that so it is genuinely covered, not just
    # coincidentally enough.
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers=headers,
    )
    assert adj.status_code == 200, adj.text
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "in_stock"
    assert row["missing_materials"] == []
    # SUB-1's 45-day lead time must not leak into ready_by: it is manufactured,
    # so only the buyable SEAL-V2 (fully stocked) can set the date.
    assert row["ready_by"] == str(_today())


async def test_member_cannot_set_planned_quantity(client, test_engine):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    org_id = me.json()["id"]

    # No invite flow exists yet to produce a non-owner user via the API, so
    # construct one directly against the test DB, same as
    # test_bom.test_rbac_blocks_non_owner_from_mutations.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        member = User(
            org_id=uuid.UUID(org_id),
            email="member@example.com",
            hashed_password=hash_password(PASSWORD),
            role="member",
            is_active=True,
        )
        session.add(member)
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    member_token = login.json()["access_token"]

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "0"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert res.status_code == 403

    # The grid itself stays open to everyone.
    read = await client.get(
        "/planning/grid", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert read.status_code == 200


async def test_grid_window_is_fixed_90_days_from_today(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 10)

    grid = await _grid(client, token)

    today = datetime.now(timezone.utc).date()
    assert grid["horizon_start"] == today.isoformat()
    assert grid["horizon_end"] == (today + timedelta(days=GRID_HORIZON_DAYS)).isoformat()


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
    assert grid_b["to_buy"] == []


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def test_ready_by_is_todays_date_plus_the_longest_lead_time(client):
    token = await _register_and_login(client)
    fast = await _create_item(client, token, "FAST-1", lead_time_days=3)
    slow = await _create_item(client, token, "SLOW-1", lead_time_days=21)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(fast, 1), (slow, 1)])

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] == str(_today() + timedelta(days=21))


async def test_ready_by_is_today_when_everything_is_in_stock(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=30)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    adj = await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "500", "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert adj.status_code == 200, adj.text
    assert adj.json()["applied"] is True

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] == str(_today())


async def test_ready_by_is_null_when_nothing_is_planned(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["ready_by"] is None


async def test_to_buy_lists_shortfalls_with_supplier_and_urgency(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post("/suppliers", json={"name": "Sealtech"}, headers=headers)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=5, min_order_qty=1000)
    patch = await client.patch(
        f"/items/{seal['id']}",
        json={"preferred_supplier_id": supplier.json()["id"]},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50, needed_by=str(_today() + timedelta(days=8)))
    grid = await _grid(client, token)

    assert len(grid["to_buy"]) == 1
    row = grid["to_buy"][0]
    assert row["item_sku"] == "SEAL-V2"
    assert row["supplier_name"] == "Sealtech"
    assert row["quantity"] == "1000.0000", "min order quantity must round the order up"
    assert row["order_by"] == str(_today() + timedelta(days=3))
    assert row["urgency"] == "soon"


async def test_to_buy_marks_a_past_order_date_overdue(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=30)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    await _plan(client, token, pump, 10, needed_by=str(_today() + timedelta(days=5)))
    grid = await _grid(client, token)

    assert grid["to_buy"][0]["urgency"] == "overdue"
    assert grid["to_buy"][0]["supplier_name"] is None


async def test_to_buy_urgency_boundary_is_soon_at_7_days_and_later_at_8(client):
    token = await _register_and_login(client)
    seal_soon = await _create_item(client, token, "SEAL-SOON", lead_time_days=0)
    pump_soon = await _create_item(client, token, "PUMP-SOON")
    await _create_active_bom(client, token, pump_soon, [(seal_soon, 1)])
    seal_later = await _create_item(client, token, "SEAL-LATER", lead_time_days=0)
    pump_later = await _create_item(client, token, "PUMP-LATER")
    await _create_active_bom(client, token, pump_later, [(seal_later, 1)])

    await _plan(client, token, pump_soon, 10, needed_by=str(_today() + timedelta(days=7)))
    await _plan(client, token, pump_later, 10, needed_by=str(_today() + timedelta(days=8)))
    grid = await _grid(client, token)

    by_sku = {row["item_sku"]: row for row in grid["to_buy"]}
    assert by_sku["SEAL-SOON"]["order_by"] == str(_today() + timedelta(days=7))
    assert by_sku["SEAL-SOON"]["urgency"] == "soon"
    assert by_sku["SEAL-LATER"]["order_by"] == str(_today() + timedelta(days=8))
    assert by_sku["SEAL-LATER"]["urgency"] == "later"


async def test_to_buy_excludes_manufactured_items(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    await _plan(client, token, pump, 10)
    grid = await _grid(client, token)

    assert [row["item_sku"] for row in grid["to_buy"]] == ["SEAL-V2"]


async def test_detail_carries_the_product_and_its_components(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    detail = _product(grid, "PUMP-A2")["detail"]
    assert [line["item_sku"] for line in detail] == ["PUMP-A2", "SEAL-V2"]
    assert detail[0]["is_manufactured"] is True
    # Compare the number, not its string form: BOM explosion multiplies through
    # quantity * (1 + scrap_pct/100), so the engine's Decimal carries more
    # places than a DB-round-tripped value would.
    assert Decimal(detail[1]["gross_demand"]) == Decimal("100")
    assert Decimal(detail[1]["net_requirement"]) == Decimal("100")


async def test_detail_is_empty_when_nothing_is_planned(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["detail"] == []


async def test_suggested_qty_is_absent_without_consumption_history(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 5)

    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["suggested_qty"] is None


async def test_suggested_qty_is_offered_only_while_nothing_is_planned(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    widget = await _create_item(client, token, "WIDGET-1")
    await client.post(
        f"/inventory/{widget['id']}/adjust",
        json={"quantity": "1000", "reason_code": "found_stock"},
        headers=headers,
    )
    for _ in range(10):
        consume = await client.post(
            f"/inventory/{widget['id']}/consume", json={"quantity": "7"}, headers=headers
        )
        assert consume.status_code == 200, consume.text

    grid = await _grid(client, token)
    suggested = _product(grid, "WIDGET-1")["suggested_qty"]
    assert suggested is not None and Decimal(suggested) > 0

    await _plan(client, token, widget, 5)
    grid_after = await _grid(client, token)
    assert _product(grid_after, "WIDGET-1")["suggested_qty"] is None


async def test_a_bom_component_with_consumption_history_still_does_not_get_its_own_row(client):
    """Consumption history widens which items count as products, but a
    component of an active BOM must never surface as one - it is still
    reachable only through its parent's row."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "1000", "reason_code": "found_stock"},
        headers=headers,
    )
    consume = await client.post(
        f"/inventory/{seal['id']}/consume", json={"quantity": "7"}, headers=headers
    )
    assert consume.status_code == 200, consume.text

    grid = await _grid(client, token)

    assert {row["item_sku"] for row in grid["products"]} == {"PUMP-A2"}
