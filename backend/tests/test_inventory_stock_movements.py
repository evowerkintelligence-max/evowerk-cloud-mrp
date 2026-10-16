from __future__ import annotations

from decimal import Decimal

import pytest

from tests._inventory_helpers import (
    PASSWORD,
    _dec,
    _register_and_login,
    _create_item,
    _create_supplier,
    _receive,
    _on_hand,
    _add_user_with_role,
    _login,
)

pytestmark = pytest.mark.asyncio


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


async def test_manager_can_consume_and_transfer(client, test_engine):
    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    other_loc = await client.post(
        "/locations", json={"code": "WH2", "name": "Warehouse 2"}, headers=headers
    )
    other_location_id = other_loc.json()["id"]
    default_location_id = next(
        loc["id"] for loc in (await client.get("/locations", headers=headers)).json() if loc["is_default"]
    )

    await _add_user_with_role(test_engine, org_id, "manager1@example.com", "manager")
    manager_headers = await _login(client, "manager1@example.com")

    consume = await client.post(
        f"/inventory/{item['id']}/consume", json={"quantity": "10"}, headers=manager_headers
    )
    assert consume.status_code == 200
    assert _dec(consume.json()["quantity_on_hand"]) == Decimal("40")

    transfer = await client.post(
        f"/inventory/{item['id']}/transfer",
        json={
            "from_location_id": default_location_id,
            "to_location_id": other_location_id,
            "quantity": "5",
        },
        headers=manager_headers,
    )
    assert transfer.status_code == 200
    balances = {b["location_id"]: _dec(b["quantity_on_hand"]) for b in transfer.json()["by_location"]}
    assert balances[other_location_id] == Decimal("5")
    assert balances[default_location_id] == Decimal("35")


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
                hashed_password=hash_password(PASSWORD),
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
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=member_headers,
        )
    ).status_code == 403
    # Reads allowed.
    assert (await client.get("/inventory", headers=member_headers)).status_code == 200
