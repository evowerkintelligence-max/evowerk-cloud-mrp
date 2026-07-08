from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


def _dec(v) -> Decimal:
    return Decimal(str(v))


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


async def _receive(client, token, item, supplier, qty, lot_number=None):
    """Order `qty`, submit, and receive it. Returns the PO id."""
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
    body = {"po_line_id": line_id, "quantity": str(qty)}
    if lot_number:
        body["lot_number"] = lot_number
    recv = await client.post(f"/purchase-orders/{po_id}/receive", json=[body], headers=headers)
    assert recv.status_code == 200
    return po_id


async def _on_hand(client, token, item):
    res = await client.get(f"/inventory/{item['id']}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    return res.json()


async def test_receiving_non_lot_item_accrues_on_hand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    await _receive(client, token, item, supplier, 40)
    await _receive(client, token, item, supplier, 25)  # accumulates

    listing = await client.get("/inventory", headers=headers)
    assert len(listing.json()) == 1
    assert _dec(listing.json()[0]["quantity_on_hand"]) == Decimal("65")

    detail = await _on_hand(client, token, item)
    assert _dec(detail["quantity_on_hand"]) == Decimal("65")
    # Two receipt transactions, each recording a positive delta.
    types = [tx["transaction_type"] for tx in detail["transactions"]]
    assert types == ["receipt", "receipt"]
    assert [_dec(tx["quantity_delta"]) for tx in detail["transactions"]] == [Decimal("40"), Decimal("25")]


async def test_lot_tracked_item_gets_no_inventory_balance(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)

    await _receive(client, token, item, supplier, 40, lot_number="LOT-1")

    # Lot-tracked items keep on-hand in lots, not the inventory ledger.
    assert (await client.get("/inventory", headers=headers)).json() == []
    detail = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert detail.status_code == 400  # "manage through lots"


async def test_consume_reduces_on_hand_and_over_consume_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    over = await client.post(
        f"/inventory/{item['id']}/consume", json={"quantity": "51"}, headers=headers
    )
    assert over.status_code == 409

    ok = await client.post(
        f"/inventory/{item['id']}/consume",
        json={"quantity": "20", "reason": "production"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert _dec(ok.json()["quantity_on_hand"]) == Decimal("30")

    detail = await _on_hand(client, token, item)
    assert [tx["transaction_type"] for tx in detail["transactions"]] == ["receipt", "consumption"]
    assert _dec(detail["transactions"][-1]["quantity_delta"]) == Decimal("-20")


async def test_consume_nonexistent_stock_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)  # never received
    res = await client.post(
        f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=headers
    )
    assert res.status_code == 409


async def test_adjust_up_and_down(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    up = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "5", "reason": "cycle count"},
        headers=headers,
    )
    assert up.status_code == 200
    assert _dec(up.json()["quantity_on_hand"]) == Decimal("15")

    down = await client.post(
        f"/inventory/{item['id']}/adjust", json={"quantity": "-3"}, headers=headers
    )
    assert down.status_code == 200
    assert _dec(down.json()["quantity_on_hand"]) == Decimal("12")

    # An adjustment below zero is rejected, on-hand unchanged.
    too_low = await client.post(
        f"/inventory/{item['id']}/adjust", json={"quantity": "-100"}, headers=headers
    )
    assert too_low.status_code == 409
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("12")


async def test_adjust_zero_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(f"/inventory/{item['id']}/adjust", json={"quantity": "0"}, headers=headers)
    assert res.status_code == 400


async def test_consume_and_adjust_reject_lot_tracked_item(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    assert (
        await client.post(f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post(f"/inventory/{item['id']}/adjust", json={"quantity": "1"}, headers=headers)
    ).status_code == 400


async def test_mrp_plan_nets_real_inventory_on_hand(client):
    """The point of the ledger: a non-lot-tracked item's received/adjusted stock
    is real on-hand for MRP, so it reduces the net requirement."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-06-01", "quantity": "100"},
        headers=headers,
    )
    await _receive(client, token, item, supplier, 30)  # 30 on hand

    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert _dec(line["on_hand"]) == Decimal("30")
    assert _dec(line["net_requirement"]) == Decimal("70")


async def test_inventory_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    item_a = await _create_item(client, token_a)
    supplier_a = await _create_supplier(client, token_a)
    await _receive(client, token_a, item_a, supplier_a, 10)

    assert (await client.get("/inventory", headers=headers_b)).json() == []
    # Org B can't see or mutate Org A's item.
    assert (await client.get(f"/inventory/{item_a['id']}", headers=headers_b)).status_code == 404
    assert (
        await client.post(f"/inventory/{item_a['id']}/consume", json={"quantity": "1"}, headers=headers_b)
    ).status_code == 404


async def test_rbac_blocks_non_owner_from_inventory_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (
        await client.post(f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=member_headers)
    ).status_code == 403
    assert (
        await client.post(f"/inventory/{item['id']}/adjust", json={"quantity": "1"}, headers=member_headers)
    ).status_code == 403
    # Reads allowed.
    assert (await client.get("/inventory", headers=member_headers)).status_code == 200
