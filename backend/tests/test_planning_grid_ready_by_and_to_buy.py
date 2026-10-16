from __future__ import annotations

from datetime import timedelta

import pytest

from tests._planning_grid_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _plan,
    _grid,
    _product,
    _today,
    _raise_job,
)

pytestmark = pytest.mark.asyncio


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

    row = _product(grid, "PUMP-A2")
    assert row["ready_by"] is None
    assert row["ready_by_unknown"] is False, (
        "nothing planned is a different situation from an unknown lead time and must not "
        "read the same way to the caller"
    )


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
    assert row["ready_by_unknown"] is True, (
        "something is planned here, so the null must be flagged as an unknown lead time, "
        "not read as nothing scheduled"
    )


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
    assert row["ready_by_unknown"] is True


async def test_arriving_is_not_drafted_when_covered_by_a_submitted_po(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=10)
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
    assert row["arriving_is_drafted"] is False, (
        "a submitted PO has actually gone to the supplier, so this must not read as drafted"
    )


async def test_arriving_is_drafted_when_covered_only_by_a_draft_po(client):
    """A draft PO is still being edited and can be changed or cancelled
    outright, unlike a submitted one - see PurchaseOrder status handling. A
    row covered only by a draft must not read "Arriving" as if something had
    actually been sent to a supplier."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2", lead_time_days=10)
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
    # Deliberately never submitted: the PO stays a draft.

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    row = _product(grid, "PUMP-A2")
    assert row["materials_status"] == "arriving"
    assert row["arriving_is_drafted"] is True


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


async def test_a_scheduled_jobs_component_gets_urgency_from_the_jobs_own_date(client):
    """A scheduled job's components used to land in to_buy with urgency
    "unknown" no matter how soon the job itself was scheduled, because
    load_scheduled_component_demand seeded quantity but no date - only
    forecast/committed demand carried one. PUMP-1 here has no forecast and no
    sales order at all, so the only date in the system is the job's own
    scheduled_start."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1", lead_time_days=5)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _raise_job(client, token, pump, qty=50, scheduled_start=str(_today() + timedelta(days=8)))

    grid = await _grid(client, token)
    to_buy = {r["item_sku"]: r for r in grid["to_buy"]}
    assert "SEAL-1" in to_buy, "the job's own component demand must reach to_buy"
    assert to_buy["SEAL-1"]["order_by"] == str(_today() + timedelta(days=3)), (
        "order_by = the job's scheduled_start minus the component's lead time"
    )
    assert to_buy["SEAL-1"]["urgency"] == "soon"


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
