from __future__ import annotations

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
    return login.json()["access_token"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "raw_material",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _create_location(client, token, code, name="Warehouse"):
    return await client.post(
        "/locations", json={"code": code, "name": name}, headers={"Authorization": f"Bearer {token}"}
    )


async def _default_location_id(client, token):
    locs = (await client.get("/locations", headers={"Authorization": f"Bearer {token}"})).json()
    return next(loc["id"] for loc in locs if loc["is_default"])


async def _create_submitted_po(client, token, item, supplier, qty="100"):
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": qty, "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    return po_id, line_id


async def test_receiving_lot_tracked_item_requires_lot_number(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier)

    res = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10"}],
        headers=headers,
    )
    assert res.status_code == 400


async def test_receiving_non_lot_tracked_item_rejects_lot_number(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=False)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier)

    res = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "L1"}],
        headers=headers,
    )
    assert res.status_code == 400


async def test_lot_created_on_receipt_and_topped_up_on_repeat_lot_number(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")

    receive1 = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "40", "lot_number": "LOT-A", "expiry_date": "2027-01-01"}],
        headers=headers,
    )
    assert receive1.status_code == 200

    lots = await client.get("/lots", headers=headers)
    assert lots.status_code == 200
    assert len(lots.json()) == 1
    lot = lots.json()[0]
    assert lot["lot_number"] == "LOT-A"
    assert lot["quantity_received"] == "40.0000"
    assert lot["quantity_remaining"] == "40.0000"
    assert lot["status"] == "active"

    # Second shipment, same lot_number - tops up, doesn't duplicate.
    receive2 = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "30", "lot_number": "LOT-A"}],
        headers=headers,
    )
    assert receive2.status_code == 200

    lots_after = await client.get("/lots", headers=headers)
    assert len(lots_after.json()) == 1
    assert lots_after.json()[0]["quantity_received"] == "70.0000"
    assert lots_after.json()[0]["quantity_remaining"] == "70.0000"


async def test_expiry_mismatch_on_topup_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")

    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-B", "expiry_date": "2027-01-01"}],
        headers=headers,
    )
    mismatched = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-B", "expiry_date": "2027-06-01"}],
        headers=headers,
    )
    assert mismatched.status_code == 409


async def test_consume_reduces_remaining_and_over_consumption_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "50", "lot_number": "LOT-C"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    over = await client.post(
        f"/lots/{lot_id}/consume", json={"quantity": "51"}, headers=headers
    )
    assert over.status_code == 409

    consume1 = await client.post(
        f"/lots/{lot_id}/consume", json={"quantity": "20", "reason": "production"}, headers=headers
    )
    assert consume1.status_code == 200
    assert consume1.json()["quantity_remaining"] == "30.0000"
    assert consume1.json()["status"] == "active"

    consume2 = await client.post(
        f"/lots/{lot_id}/consume", json={"quantity": "30", "reason": "production"}, headers=headers
    )
    assert consume2.status_code == 200
    assert consume2.json()["quantity_remaining"] == "0.0000"
    assert consume2.json()["status"] == "depleted"

    detail = await client.get(f"/lots/{lot_id}", headers=headers)
    types = [tx["transaction_type"] for tx in detail.json()["transactions"]]
    assert types == ["receipt", "consumption", "consumption"]


async def test_hold_blocks_consume_release_allows_it_again(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="100")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "50", "lot_number": "LOT-D"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    hold = await client.post(f"/lots/{lot_id}/hold", headers=headers)
    assert hold.status_code == 200
    assert hold.json()["status"] == "on_hold"

    blocked = await client.post(f"/lots/{lot_id}/consume", json={"quantity": "1"}, headers=headers)
    assert blocked.status_code == 409

    release = await client.post(f"/lots/{lot_id}/release", headers=headers)
    assert release.status_code == 200
    assert release.json()["status"] == "active"

    allowed = await client.post(f"/lots/{lot_id}/consume", json={"quantity": "1"}, headers=headers)
    assert allowed.status_code == 200

    # release when not on hold is rejected
    re_release = await client.post(f"/lots/{lot_id}/release", headers=headers)
    assert re_release.status_code == 409


async def test_lot_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    item = await _create_item(client, token_a, is_lot_tracked=True)
    supplier = await _create_supplier(client, token_a)
    po_id, line_id = await _create_submitted_po(client, token_a, item, supplier, qty="10")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-E"}],
        headers=headers_a,
    )
    lot_id = (await client.get("/lots", headers=headers_a)).json()[0]["id"]

    lots_b = await client.get("/lots", headers=headers_b)
    assert lots_b.json() == []

    get_as_b = await client.get(f"/lots/{lot_id}", headers=headers_b)
    assert get_as_b.status_code == 404

    consume_as_b = await client.post(f"/lots/{lot_id}/consume", json={"quantity": "1"}, headers=headers_b)
    assert consume_as_b.status_code == 404


async def test_rbac_blocks_non_owner_from_lot_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="10")
    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-F"}],
        headers=headers,
    )
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]

    # Same rationale as test_bom.py/test_procurement.py's RBAC tests:
    # register() always creates "owner" users, so a non-owner has to be
    # constructed directly.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        member = User(
            org_id=uuid_module.UUID(org_id),
            email="member2@example.com",
            hashed_password=hash_password(PASSWORD),
            role="member",
            is_active=True,
        )
        session.add(member)
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.post(f"/lots/{lot_id}/consume", json={"quantity": "1"}, headers=member_headers)).status_code == 403
    assert (await client.post(f"/lots/{lot_id}/hold", headers=member_headers)).status_code == 403
    assert (await client.get("/lots", headers=member_headers)).status_code == 200


async def test_lot_creation_allocates_sequentially(client):
    """Mirrors test_po_number_allocates_sequentially's documented limitation:
    the in-memory SQLite test DB's single StaticPool connection can't safely
    simulate the actual IntegrityError-retry race, so this only covers the
    happy path the guard falls back to - repeated distinct lot_numbers for
    the same item all succeed without colliding."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="500")

    for i in range(5):
        res = await client.post(
            f"/purchase-orders/{po_id}/receive",
            json=[{"po_line_id": line_id, "quantity": "10", "lot_number": f"LOT-SEQ-{i}"}],
            headers=headers,
        )
        assert res.status_code == 200

    lots = await client.get("/lots", headers=headers)
    assert len(lots.json()) == 5


async def test_receive_defaults_to_default_location(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    po_id, line_id = await _create_submitted_po(client, token, item, supplier, qty="10")
    default_id = await _default_location_id(client, token)

    await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "10", "lot_number": "LOT-LOC1"}],
        headers=headers,
    )
    lot = (await client.get("/lots", headers=headers)).json()[0]
    assert lot["location_id"] == default_id
    assert lot["location_code"] == "MAIN"


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
