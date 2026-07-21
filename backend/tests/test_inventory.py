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


async def _add_user_with_role(test_engine, org_id: str, email: str, role: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import uuid as uuid_module

    from app.core.security import hash_password
    from app.models.user import User

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email=email,
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=True,
            )
        )
        await session.commit()


async def _login(client, email: str) -> dict[str, str]:
    res = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


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


async def test_adjust_up_and_down(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    up = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "5", "reason_code": "correction"},
        headers=headers,
    )
    assert up.status_code == 200
    up_body = up.json()
    assert up_body["applied"] is True
    assert _dec(up_body["item"]["quantity_on_hand"]) == Decimal("15")

    down = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-3", "reason_code": "shrinkage"},
        headers=headers,
    )
    assert down.status_code == 200
    assert _dec(down.json()["item"]["quantity_on_hand"]) == Decimal("12")

    # An adjustment below zero is rejected, on-hand unchanged.
    too_low = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-100", "reason_code": "correction"},
        headers=headers,
    )
    assert too_low.status_code == 409
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("12")


async def test_adjust_zero_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "0", "reason_code": "correction"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_adjust_requires_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust", json={"quantity": "1"}, headers=headers
    )
    assert res.status_code == 422


async def test_adjust_rejects_unknown_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "made_up"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_adjust_other_reason_requires_note(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    missing_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other"},
        headers=headers,
    )
    assert missing_note.status_code == 422

    with_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other", "reason_note": "warehouse fire, see incident report"},
        headers=headers,
    )
    assert with_note.status_code == 200


async def test_consume_and_adjust_reject_lot_tracked_item(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    assert (
        await client.post(f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=headers,
        )
    ).status_code == 400


async def test_manager_adjust_within_threshold_applies_immediately(client, test_engine):
    token = await _register_and_login(client, email="owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager4@example.com", "manager")
    manager_headers = await _login(client, "manager4@example.com")

    # default threshold is 50; a delta of 10 is within it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "10", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("110")


async def test_manager_adjust_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager5@example.com", "manager")
    manager_headers = await _login(client, "manager5@example.com")

    # default threshold is 50; a delta of 60 exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert body["request"]["status"] == "pending"
    assert _dec(body["request"]["quantity_delta"]) == Decimal("60")
    assert _dec(body["request"]["expected_quantity_at_submit"]) == Decimal("100")

    # on-hand is unchanged - nothing was posted
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("100")


async def test_owner_adjust_over_threshold_still_applies_immediately(client):
    token = await _register_and_login(client, email="owner6@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("160")


async def test_count_computes_variance_and_applies_within_threshold(client):
    token = await _register_and_login(client, email="owner7@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "42"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("42")

    detail = await _on_hand(client, token, item)
    last_tx = detail["transactions"][-1]
    assert last_tx["transaction_type"] == "adjustment"
    assert _dec(last_tx["quantity_delta"]) == Decimal("-8")
    assert _dec(last_tx["counted_quantity"]) == Decimal("42")
    assert _dec(last_tx["expected_quantity"]) == Decimal("50")
    assert last_tx["reason"] == "cycle_count"


async def test_count_no_variance_rejected(client):
    token = await _register_and_login(client, email="owner8@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "50"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_count_rejects_negative_counted_quantity(client):
    token = await _register_and_login(client, email="owner8b@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "-1"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_count_against_no_balance_treats_expected_as_zero(client):
    token = await _register_and_login(client, email="owner9@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)  # never received - no balance row

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "5"},
        headers=headers,
    )
    assert res.status_code == 200
    assert _dec(res.json()["item"]["quantity_on_hand"]) == Decimal("5")


async def test_manager_count_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner10@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager10@example.com", "manager")
    manager_headers = await _login(client, "manager10@example.com")

    # default threshold is 50; counting 20 (variance -80) exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "20"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert _dec(body["request"]["quantity_delta"]) == Decimal("-80")
    assert _dec(body["request"]["counted_quantity"]) == Decimal("20")
    assert body["request"]["reason_code"] == "cycle_count"


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
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=member_headers,
        )
    ).status_code == 403
    # Reads allowed.
    assert (await client.get("/inventory", headers=member_headers)).status_code == 200
