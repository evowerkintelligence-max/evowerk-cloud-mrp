from __future__ import annotations

import pytest

from tests._lot_tracking_helpers import (
    _register_and_login,
    _create_item,
    _create_supplier,
    _create_location,
    _default_location_id,
    _create_submitted_po,
)

pytestmark = pytest.mark.asyncio


async def test_receive_at_named_location_and_repeat_lot_number_scoped_per_location(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")

    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "40", "lot_number": "LOT-SPLIT"}],
        headers=headers,
    )
    res = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "25",
                "lot_number": "LOT-SPLIT",
                "location_id": wh2["id"],
            }
        ],
        headers=headers,
    )
    assert res.status_code == 200

    # Same lot_number split across two locations is two Lot rows, each with its
    # own quantity_remaining - not merged into one, and not rejected as a dup.
    lots = (await client.get("/lots", headers=headers)).json()
    assert len(lots) == 2
    by_location = {lot["location_code"]: lot for lot in lots}
    assert by_location["MAIN"]["quantity_remaining"] == "40.0000"
    assert by_location["WH2"]["quantity_remaining"] == "25.0000"


async def test_consume_is_location_scoped(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "20", "lot_number": "LOT-CS", "location_id": wh2["id"]}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    # Only 20 exists, all at WH2 - consuming more than that from this lot 409s
    # regardless of how much of the same lot_number sits elsewhere.
    over = await client.post(f"/lots/{lot_id}/consume", json={"quantity": "21"}, headers=headers)
    assert over.status_code == 409


async def test_transfer_moves_stock_between_locations(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "50", "lot_number": "LOT-TR"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    transfer = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": wh2["id"], "quantity": "30"},
        headers=headers,
    )
    assert transfer.status_code == 200
    assert transfer.json()["quantity_remaining"] == "20.0000"

    lots = (await client.get("/lots", headers=headers)).json()
    by_location = {lot["location_code"]: lot for lot in lots}
    assert by_location["MAIN"]["quantity_remaining"] == "20.0000"
    assert by_location["WH2"]["quantity_remaining"] == "30.0000"
    assert by_location["WH2"]["lot_number"] == "LOT-TR"

    detail = await client.get(f"/lots/{lot_id}", headers=headers)
    types = [tx["transaction_type"] for tx in detail.json()["transactions"]]
    assert types == ["receipt", "transfer_out"]


async def test_transfer_insufficient_quantity_rejected_and_same_location_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    default_id = await _default_location_id(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-TR2"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    over = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": wh2["id"], "quantity": "11"},
        headers=headers,
    )
    assert over.status_code == 409

    same = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": default_id, "quantity": "1"},
        headers=headers,
    )
    assert same.status_code == 400

    # A single 100% transfer leaves the source lot at zero and depleted - the
    # transfer isn't rejected just because the source dries up entirely.
    full = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": wh2["id"], "quantity": "10"},
        headers=headers,
    )
    assert full.status_code == 200
    assert full.json()["quantity_remaining"] == "0.0000"
    assert full.json()["status"] == "depleted"


async def test_on_hold_lot_cannot_be_transferred(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="10")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-HOLD"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]
    await client.post(f"/lots/{lot_id}/hold", headers=headers)

    blocked = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": wh2["id"], "quantity": "1"},
        headers=headers,
    )
    assert blocked.status_code == 409


async def test_mrp_sums_lot_on_hand_across_locations(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="FG-LOC", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="25")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "15", "lot_number": "LOT-MRP"}],
        headers=headers,
    )
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "10",
                "lot_number": "LOT-MRP",
                "location_id": wh2["id"],
            }
        ],
        headers=headers,
    )
    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2027-01-01", "quantity": "30"},
        headers=headers,
    )

    plan = await client.get(
        "/mrp/plan?horizon_start=2027-01-01&horizon_end=2027-01-31", headers=headers
    )
    assert plan.status_code == 200
    line = next(row for row in plan.json()["lines"] if row["item_id"] == item["id"])
    # 30 demand - (15 at MAIN + 10 at WH2) = 5, not 30 - 15 = 15 or 30 - 10 = 20 -
    # on-hand nets against the sum across both locations.
    assert line["net_requirement"] == "5.0000"


async def test_lot_location_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orgloca@example.com", org_name="OrgLocA")
    token_b = await _register_and_login(client, email="orglocb@example.com", org_name="OrgLocB")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    item = await _create_item(client, token_a, is_lot_tracked=True)
    supplier = await _create_supplier(client, token_a)
    po_id, line_id = await _create_submitted_po(client, token_a, item, supplier, qty="10")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-XT"}],
        headers=headers_a,
    )
    lot_id = (await client.get("/lots", headers=headers_a)).json()[0]["id"]
    b_default = await _default_location_id(client, token_b)

    cross = await client.post(
        f"/lots/{lot_id}/transfer",
        json={"to_location_id": b_default, "quantity": "1"},
        headers=headers_a,
    )
    # b_default isn't a location in org A - resolve_location_id 404s it.
    assert cross.status_code == 404


async def test_list_lots_status_location_filters_and_pagination(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="FILT", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="30")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-1"}],
        headers=headers,
    )
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-2", "location_id": wh2["id"]}],
        headers=headers,
    )
    lots = (await client.get("/lots", headers=headers)).json()
    lot2_id = next(lot["id"] for lot in lots if lot["lot_number"] == "LOT-2")
    await client.post(f"/lots/{lot2_id}/hold", headers=headers)

    by_status = await client.get("/lots?status=on_hold", headers=headers)
    assert by_status.status_code == 200
    assert [lot["lot_number"] for lot in by_status.json()] == ["LOT-2"]

    by_location = await client.get(f"/lots?location_id={wh2['id']}", headers=headers)
    assert [lot["lot_number"] for lot in by_location.json()] == ["LOT-2"]

    bad_status = await client.get("/lots?status=bogus", headers=headers)
    assert bad_status.status_code == 400

    page1 = (await client.get("/lots?limit=1&offset=0", headers=headers)).json()
    page2 = (await client.get("/lots?limit=1&offset=1", headers=headers)).json()
    assert len(page1) == 1 and len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


async def test_consume_fefo_splits_across_lots_earliest_expiry_first(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="FEFO-1", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    location_id = await _default_location_id(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="25")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-EARLY", "expiry_date": "2026-08-01"},
            {"po_line_id": line_id, "quantity": "15", "lot_number": "LOT-LATE", "expiry_date": "2027-01-01"},
        ],
        headers=headers,
    )

    res = await client.post(
        "/lots/consume-fefo",
        json={"item_id": item["id"], "location_id": location_id, "quantity": "12", "reason": "production"},
        headers=headers,
    )
    assert res.status_code == 200
    lines = res.json()["lines"]
    assert [line["lot_number"] for line in lines] == ["LOT-EARLY", "LOT-LATE"]
    assert lines[0]["quantity_consumed"] == "10.0000"
    assert lines[1]["quantity_consumed"] == "2.0000"

    lots_by_number = {lot["lot_number"]: lot for lot in (await client.get("/lots", headers=headers)).json()}
    assert lots_by_number["LOT-EARLY"]["quantity_remaining"] == "0.0000"
    assert lots_by_number["LOT-EARLY"]["status"] == "depleted"
    assert lots_by_number["LOT-LATE"]["quantity_remaining"] == "13.0000"


async def test_consume_fefo_insufficient_stock_rejected_writes_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="FEFO-2", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    location_id = await _default_location_id(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="5")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "5", "lot_number": "LOT-SHORT"}],
        headers=headers,
    )

    res = await client.post(
        "/lots/consume-fefo",
        json={"item_id": item["id"], "location_id": location_id, "quantity": "10"},
        headers=headers,
    )
    assert res.status_code == 409

    lot = (await client.get("/lots", headers=headers)).json()[0]
    assert lot["quantity_remaining"] == "5.0000"  # rejected request wrote nothing


async def test_morning_brief_lot_alerts_flag_on_hold_and_expiring_soon(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="BRIEF-1", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="30")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            # Within the 14-day warning window as of "today" in this test env.
            {"po_line_id": line_id, "quantity": "5", "lot_number": "LOT-SOON", "expiry_date": "2026-07-15"},
            # Far enough out to not be flagged.
            {"po_line_id": line_id, "quantity": "5", "lot_number": "LOT-LATER", "expiry_date": "2027-06-01"},
            {"po_line_id": line_id, "quantity": "5", "lot_number": "LOT-HOLD"},
        ],
        headers=headers,
    )
    lots_by_number = {lot["lot_number"]: lot for lot in (await client.get("/lots", headers=headers)).json()}
    await client.post(f"/lots/{lots_by_number['LOT-HOLD']['id']}/hold", headers=headers)

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    alerts_by_number = {a["lot_number"]: a for a in res.json()["lot_alerts"]}
    assert set(alerts_by_number) == {"LOT-SOON", "LOT-HOLD"}
    assert alerts_by_number["LOT-SOON"]["reason"] == "expiring_soon"
    assert alerts_by_number["LOT-HOLD"]["reason"] == "on_hold"
