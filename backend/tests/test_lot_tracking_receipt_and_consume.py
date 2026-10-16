from __future__ import annotations

import pytest

from tests._lot_tracking_helpers import (
    PASSWORD,
    _register_and_login,
    _create_item,
    _create_supplier,
    _default_location_id,
    _create_submitted_po,
)

pytestmark = pytest.mark.asyncio


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

    # Same rationale as test_bom_lifecycle.py/test_procurement_purchase_orders.py's RBAC tests:
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
