from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.inventory import InventoryTransaction
from app.models.user import User
from app.services.planning_grid import GRID_HORIZON_DAYS

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


async def _create_item(
    client, token, sku, name="Item", lead_time_days=None, min_order_qty=None, safety_stock_qty=None
):
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
            "safety_stock_qty": str(safety_stock_qty) if safety_stock_qty is not None else None,
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
    """Set the grid's planned quantity for an item. Defaults needed_by to two
    weeks out for tests that plan for the first time and don't care about the
    date - the endpoint itself no longer invents one, so callers that only
    want a quantity in place still need to supply something."""
    if needed_by is None and Decimal(str(qty)) > 0:
        needed_by = str(_today() + timedelta(days=14))
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

    today = datetime.now(timezone.utc).date()
    needed_by = str(today + timedelta(days=10))
    await _plan(client, token, widget, 10, needed_by=needed_by)
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "10.0000"
    assert Decimal(row["on_hand"]) == Decimal(0)
    assert row["needed_by"] == needed_by


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


async def test_manager_can_set_planned_quantity(client, test_engine):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        manager = User(
            org_id=uuid.UUID(org_id),
            email="manager@example.com",
            hashed_password=hash_password(PASSWORD),
            role="manager",
            is_active=True,
        )
        session.add(manager)
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "manager@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    manager_token = login.json()["access_token"]

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "10", "needed_by": str(_today() + timedelta(days=10))},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert res.status_code == 200, res.text


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


async def test_ready_by_is_null_when_a_short_component_has_no_lead_time_on_file(client):
    """lead_time_days is nullable and null means "don't know", never a
    same-day lead time (see Item.lead_time_days). A missing component with no
    lead time on file must not render as ready today."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")  # lead_time_days left unset (None)
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "missing"
    assert row["ready_by"] is None


async def test_ready_by_is_null_when_an_arriving_component_has_no_lead_time_on_file(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")  # lead_time_days left unset (None)
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
    assert row["ready_by"] is None


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


async def test_to_buy_urgency_is_unknown_when_the_component_has_no_lead_time_on_file(client):
    """No lead time on file means no order-by date can be computed (see
    Item.lead_time_days and mrp.suggested_order_date). That must not be
    presented as "later" - the least urgent thing to buy - so it gets its own
    urgency and is not sorted after a genuinely low-urgency item."""
    token = await _register_and_login(client)
    unknown_seal = await _create_item(client, token, "SEAL-UNKNOWN")  # lead_time_days left unset
    unknown_pump = await _create_item(client, token, "PUMP-UNKNOWN")
    await _create_active_bom(client, token, unknown_pump, [(unknown_seal, 1)])
    later_seal = await _create_item(client, token, "SEAL-LATER", lead_time_days=0)
    later_pump = await _create_item(client, token, "PUMP-LATER")
    await _create_active_bom(client, token, later_pump, [(later_seal, 1)])

    await _plan(client, token, unknown_pump, 10, needed_by=str(_today() + timedelta(days=30)))
    await _plan(client, token, later_pump, 10, needed_by=str(_today() + timedelta(days=30)))
    grid = await _grid(client, token)

    by_sku = {row["item_sku"]: row for row in grid["to_buy"]}
    assert by_sku["SEAL-UNKNOWN"]["order_by"] is None
    assert by_sku["SEAL-UNKNOWN"]["urgency"] == "unknown"
    assert by_sku["SEAL-LATER"]["urgency"] == "later"
    skus_in_order = [row["item_sku"] for row in grid["to_buy"]]
    assert skus_in_order.index("SEAL-UNKNOWN") < skus_in_order.index("SEAL-LATER"), (
        "an unknown lead time must not sort after a known, low-urgency one"
    )


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
    # Product-first (ZPUMP-1) and alphabetical (ASEAL-1) disagree here, so this
    # only passes if the "product floats to the top" sort key is real.
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "ASEAL-1")
    pump = await _create_item(client, token, "ZPUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    detail = _product(grid, "ZPUMP-1")["detail"]
    assert [line["item_sku"] for line in detail] == ["ZPUMP-1", "ASEAL-1"]
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
    # An active BOM guarantees PUMP-A2 a row (Fix 1's consumed-only filter does
    # not apply to it), with no plan and no consumption history to suggest from.
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["suggested_qty"] is None


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
    # WIDGET-1 has no BOM and no plan - a suggestion is its only claim to a
    # row, so the row must be present (Fix 1's consumed-only filter keeps it).
    assert "WIDGET-1" in {row["item_sku"] for row in grid["products"]}
    suggested = _product(grid, "WIDGET-1")["suggested_qty"]
    assert suggested is not None and Decimal(suggested) > 0

    await _plan(client, token, widget, 5)
    grid_after = await _grid(client, token)
    assert _product(grid_after, "WIDGET-1")["suggested_qty"] is None


async def test_consumed_only_item_without_a_suggestion_gets_no_row(client, test_engine):
    """SEAL-V2 has no BOM and no forecast - consumption history is its only
    claim to a row. Its one consumption event is backdated past the
    moving-average forecast's 7-day window (but still inside the 90-day
    lookback that decides whether the item counts as "consumed" at all), so
    compute_weekly_suggestions offers nothing. Fix 1 must drop the row rather
    than show it in_stock/planned_qty 0/detail []: an empty row nobody asked
    for."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "100", "reason_code": "found_stock"},
        headers=headers,
    )
    consume = await client.post(
        f"/inventory/{seal['id']}/consume", json={"quantity": "1"}, headers=headers
    )
    assert consume.status_code == 200, consume.text

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.execute(
            update(InventoryTransaction)
            .where(InventoryTransaction.item_id == uuid.UUID(seal["id"]))
            .where(InventoryTransaction.transaction_type == "consumption")
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=30))
        )
        await session.commit()

    grid = await _grid(client, token)

    assert "SEAL-V2" not in {row["item_sku"] for row in grid["products"]}


async def test_setting_a_quantity_replaces_the_previous_one(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10, needed_by=str(_today() + timedelta(days=5)))
    await _plan(client, token, widget, 25, needed_by=str(_today() + timedelta(days=30)))
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "25.0000", "the window must hold one number, not a running total"
    assert row["needed_by"] == str(_today() + timedelta(days=30))


async def test_zero_clears_the_plan(client):
    """WIDGET-1 has no BOM and no consumption history, so clearing its plan to
    0 drops its only claim to a grid row: check the write's own response
    (the fallback row shape), not a follow-up read of the grid."""
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 10)

    cleared = await _plan(client, token, widget, 0)

    assert cleared["planned_qty"] == "0.0000"
    assert cleared["needed_by"] is None


async def test_quantity_with_no_date_and_no_existing_plan_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "10"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400
    assert "date" in res.json()["detail"].lower()


async def test_quantity_edit_without_a_date_reuses_the_existing_plan_date(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    needed_by = str(_today() + timedelta(days=9))
    await _plan(client, token, widget, 10, needed_by=needed_by)

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "25"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["planned_qty"] == "25.0000"
    assert body["needed_by"] == needed_by


async def test_zero_with_no_date_still_clears_the_plan(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "0"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["planned_qty"] == "0.0000"
    assert res.json()["needed_by"] is None


async def test_planned_bucket_count_is_one_for_a_normally_planned_item(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["planned_bucket_count"] == 1


async def test_planned_bucket_count_is_more_than_one_for_a_multi_row_plan(client):
    """An imported or legacy forecast can be spread across several dated rows
    inside the window - the frontend needs the count so it can warn before
    editing the quantity collapses them into one."""
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    headers = {"Authorization": f"Bearer {token}"}
    first = str(_today() + timedelta(days=7))
    second = str(_today() + timedelta(days=14))
    for period, qty in [(first, "5"), (second, "8")]:
        res = await client.post(
            "/forecasts",
            json={"item_id": widget["id"], "period_start": period, "quantity": qty},
            headers=headers,
        )
        assert res.status_code == 200, res.text

    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_bucket_count"] == 2
    assert row["planned_qty"] == "13.0000"
    assert row["needed_by"] == first


async def test_negative_quantity_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "-5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_oversized_quantity_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "100000000"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_date_outside_the_window_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(_today() + timedelta(days=400))},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_needed_by_of_today_is_accepted(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    plan = await _plan(client, token, widget, 5, needed_by=str(_today()))

    assert plan["needed_by"] == str(_today())


async def test_needed_by_at_the_end_of_the_horizon_is_accepted(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    edge = _today() + timedelta(days=GRID_HORIZON_DAYS)

    plan = await _plan(client, token, widget, 5, needed_by=str(edge))

    assert plan["needed_by"] == str(edge)


async def test_needed_by_before_today_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(_today() - timedelta(days=1))},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_needed_by_one_day_past_the_horizon_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    past_edge = _today() + timedelta(days=GRID_HORIZON_DAYS + 1)

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(past_edge)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_unknown_item_is_rejected(client):
    token = await _register_and_login(client)

    res = await client.put(
        "/planning/grid/00000000-0000-0000-0000-000000000000",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 404


async def test_another_orgs_item_cannot_be_planned(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert res.status_code == 404


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


async def test_set_planned_quantity_audit_log_carries_quantity_needed_by_and_rows_replaced(
    client, test_engine
):
    """The single delete-then-insert write this endpoint makes must be
    reconstructible from the audit log alone, same as the bucket endpoint it
    replaced (see forecasts.py)."""
    from sqlalchemy import select

    from app.models.audit import AuditLog

    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    needed_by = str(_today() + timedelta(days=10))
    await _plan(client, token, widget, 10, needed_by=needed_by)
    await _plan(client, token, widget, 25, needed_by=str(_today() + timedelta(days=20)))

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "forecast.planned_via_grid")
            )
        ).scalars().all()

    events = sorted(rows, key=lambda r: r.created_at)
    assert len(events) == 2
    first, second = events
    assert first.event_metadata["quantity"] == "10"
    assert first.event_metadata["needed_by"] == needed_by
    assert first.event_metadata["rows_replaced"] == 0, "nothing existed yet to replace"
    assert second.event_metadata["quantity"] == "25"
    assert second.event_metadata["rows_replaced"] == 1, "the previous plan's one row must be counted"


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


def _soon() -> str:
    return str(_today() + timedelta(days=30))


async def _create_order(client, token, customer, item, qty=10, needed_by=None):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by or _soon(),
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_committed_quantity_appears_on_the_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=40, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("40")


async def test_a_product_with_only_committed_demand_still_gets_a_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["planned_qty"]) == Decimal(0), "nothing was typed"
    assert Decimal(row["committed_qty"]) == Decimal("15")
    assert row["materials_status"] == "missing", "an order with no stock must show as short"


async def test_committed_quantity_is_zero_without_orders(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)


async def test_a_cancelled_order_leaves_nothing_committed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)
    order = await _create_order(client, token, customer, item, qty=40, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)
    assert Decimal(row["planned_qty"]) == Decimal("10")


async def test_a_committed_only_row_gets_a_ready_by_date(client):
    """A row that exists only because a customer ordered it still needs a
    date: a blank ready-by on a red row is the least useful answer here."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1", lead_time_days=5)
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("15")
    assert row["ready_by"] is not None


async def test_a_committed_product_is_not_offered_a_history_suggestion(client):
    """An order is better information than a forecast guess, so a product a
    customer has already ordered must not be offered one."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["suggested_qty"] is None


async def test_an_overdue_commitment_is_marked_on_the_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    yesterday = str(_today() - timedelta(days=1))
    await _create_order(client, token, customer, item, qty=12, needed_by=yesterday)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("12"), "a late order is still owed"
    assert row["committed_overdue"] is True


async def test_an_on_time_commitment_is_not_marked_overdue(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=12, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["committed_overdue"] is False


async def test_a_product_with_no_orders_is_not_marked_overdue(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["committed_overdue"] is False


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_the_summary_counts_jobs_needing_attention(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(
        client, token, pump, qty=10, scheduled_start=str(_today() - timedelta(days=3))
    )

    grid = await _grid(client, token)
    assert grid["summary"]["jobs_needing_attention"] == 1


async def test_the_summary_counts_no_attention_for_a_job_on_schedule(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(
        client, token, pump, qty=10, scheduled_start=str(_today() + timedelta(days=3))
    )

    grid = await _grid(client, token)
    assert grid["summary"]["jobs_needing_attention"] == 0


async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


async def test_the_row_shows_what_is_in_production(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=20)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["in_production"]) == Decimal("20")


async def test_a_row_with_materials_can_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, seal, 100)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is True


async def test_a_row_short_of_materials_cannot_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False, "offering Make on a job that would 409 is a trap"


async def test_a_product_with_no_recipe_cannot_be_made(client):
    token = await _register_and_login(client)
    pump = await _create_item(client, token, "PUMP-1")
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False


async def test_a_row_already_covered_by_a_job_cannot_be_made_again(client):
    """Nothing outstanding means nothing to raise, and offering Make would
    quietly double the plan."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False


async def test_stock_held_by_a_scheduled_job_does_not_count_as_available(client):
    """The same reservation rule start applies: material another job has
    already claimed cannot also make this row."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    other = await _create_item(client, token, "PUMP-2")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _create_active_bom(client, token, other, [(seal, 1)])
    await _give_stock(client, token, seal, 10)
    await _plan(client, token, pump, 10)
    assert _product(await _grid(client, token), "PUMP-1")["can_make"] is True

    # A job for the OTHER product, so PUMP-1's own outstanding quantity is
    # untouched and only the reservation can refuse it. Undated and numbered
    # first, so it outranks the undated job Make would raise next and holds all
    # ten seals.
    await _raise_job(client, token, other, qty=10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False, "the other job already holds every seal"


async def test_the_build_calculator_still_needs_a_scheduled_jobs_components(client):
    """The two halves of a job must move together in EVERY engine caller. The
    build calculator used to subtract a scheduled job's supply without seeding
    its component demand, so it reported that a build short of 20 seals needed
    nothing, and build-plan/generate-pos raised no PO for them."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=str(_today() + timedelta(days=5)))

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": pump["id"], "build_quantity": "10"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" in lines, lines
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "the scheduled job's seals are still needed even though its pumps count as supply"
    )


async def test_a_buffered_item_with_no_demand_still_gets_a_line(client):
    """Safety stock is a requirement in its own right. The netting loop already
    explodes it onto the item's components, so dropping the item's own line
    would attribute an order to a row the plan never shows."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1", safety_stock_qty=50)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    # Cover the pump's own demand entirely, so the seal is reached by
    # explosion with zero gross demand and only its buffer to justify a line.
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, pump, 10)

    res = await client.get("/mrp/plan", headers=headers)
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" in lines, f"the buffered item vanished from the plan: {list(lines)}"
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("50")


async def test_a_buffer_already_covered_by_stock_stays_off_the_plan(client):
    """The other side of the same gate: a buffer with nothing to do is noise,
    not a line."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1", safety_stock_qty=50)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, pump, 10)
    await _give_stock(client, token, seal, 200)

    res = await client.get("/mrp/plan", headers=headers)
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" not in lines, lines


async def test_a_row_wanted_only_by_customers_can_still_be_made(client):
    """A shop working from sales orders types nothing into the grid, so basing
    the Make quantity on the forecast alone left the firmest demand in the shop
    with no way to build it."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    customer = await _create_customer(client, token)
    await _create_order(client, token, customer, pump, qty=25)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["committed_qty"]) == Decimal("25")
    assert Decimal(row["planned_qty"]) == Decimal(0)
    assert Decimal(row["outstanding_qty"]) == Decimal("25")
    assert row["can_make"] is True


async def test_a_committed_row_already_being_made_offers_nothing_more(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    customer = await _create_customer(client, token)
    await _create_order(client, token, customer, pump, qty=25)
    job = await _raise_job(client, token, pump, qty=25)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["outstanding_qty"]) == Decimal(0)
    assert row["can_make"] is False
