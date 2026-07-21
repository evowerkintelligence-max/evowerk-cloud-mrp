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


async def _create_item(client, token, sku="RAW-1", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": "Rod", "item_type": "raw_material", "is_lot_tracked": is_lot_tracked},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token):
    return (await client.post("/suppliers", json={"name": "S"}, headers={"Authorization": f"Bearer {token}"})).json()


async def _create_location(client, token, code, name="Warehouse"):
    res = await client.post(
        "/locations", json={"code": code, "name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    return res


async def _default_location_id(client, token):
    locs = (await client.get("/locations", headers={"Authorization": f"Bearer {token}"})).json()
    return next(loc["id"] for loc in locs if loc["is_default"])


async def _receive(client, token, item, supplier, qty, location_id=None, lot_number=None):
    headers = {"Authorization": f"Bearer {token}"}
    po = (await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)).json()
    lines = await client.patch(
        f"/purchase-orders/{po['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
    body = {"po_line_id": line_id, "quantity": str(qty)}
    if location_id:
        body["location_id"] = location_id
    if lot_number:
        body["lot_number"] = lot_number
    recv = await client.post(f"/purchase-orders/{po['id']}/receive", json=[body], headers=headers)
    assert recv.status_code == 200


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
async def test_register_creates_one_default_location(client):
    token = await _register_and_login(client)
    locs = (await client.get("/locations", headers={"Authorization": f"Bearer {token}"})).json()
    assert len(locs) == 1
    assert locs[0]["is_default"] is True
    assert locs[0]["code"] == "MAIN"


async def test_create_location_and_duplicate_code_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = await _create_location(client, token, "WH2", "Second Warehouse")
    assert created.status_code == 201
    assert created.json()["is_default"] is False

    dup = await _create_location(client, token, "WH2")
    assert dup.status_code == 409
    assert len((await client.get("/locations", headers=headers)).json()) == 2  # MAIN + WH2


# --------------------------------------------------------------------------- #
# Per-location on-hand
# --------------------------------------------------------------------------- #
async def test_receive_defaults_to_default_location(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 40)  # no location -> default

    detail = (await client.get(f"/inventory/{item['id']}", headers=headers)).json()
    assert _dec(detail["quantity_on_hand"]) == Decimal("40")
    assert len(detail["by_location"]) == 1
    assert detail["by_location"][0]["location_code"] == "MAIN"


async def test_receive_and_consume_are_location_scoped(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()

    await _receive(client, token, item, supplier, 100)  # -> MAIN
    await _receive(client, token, item, supplier, 25, location_id=wh2["id"])  # -> WH2

    detail = (await client.get(f"/inventory/{item['id']}", headers=headers)).json()
    assert _dec(detail["quantity_on_hand"]) == Decimal("125")
    by_code = {lq["location_code"]: _dec(lq["quantity_on_hand"]) for lq in detail["by_location"]}
    assert by_code == {"MAIN": Decimal("100"), "WH2": Decimal("25")}

    # Consuming at WH2 draws only from WH2; can't overdraw it even though MAIN
    # has plenty.
    over = await client.post(
        f"/inventory/{item['id']}/consume",
        json={"quantity": "30", "location_id": wh2["id"]},
        headers=headers,
    )
    assert over.status_code == 409
    ok = await client.post(
        f"/inventory/{item['id']}/consume",
        json={"quantity": "25", "location_id": wh2["id"]},
        headers=headers,
    )
    assert ok.status_code == 200
    assert _dec(ok.json()["quantity_on_hand"]) == Decimal("100")  # MAIN untouched


async def test_consume_at_empty_location_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    await _receive(client, token, item, supplier, 50)  # MAIN only

    res = await client.post(
        f"/inventory/{item['id']}/consume",
        json={"quantity": "1", "location_id": wh2["id"]},
        headers=headers,
    )
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# Transfer
# --------------------------------------------------------------------------- #
async def test_transfer_moves_stock_between_locations(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    main = await _default_location_id(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    await _receive(client, token, item, supplier, 100)  # MAIN

    res = await client.post(
        f"/inventory/{item['id']}/transfer",
        json={"from_location_id": main, "to_location_id": wh2["id"], "quantity": "40"},
        headers=headers,
    )
    assert res.status_code == 200
    # Total unchanged, split across the two locations.
    assert _dec(res.json()["quantity_on_hand"]) == Decimal("100")
    by_code = {lq["location_code"]: _dec(lq["quantity_on_hand"]) for lq in res.json()["by_location"]}
    assert by_code == {"MAIN": Decimal("60"), "WH2": Decimal("40")}

    # Ledger records both sides.
    detail = (await client.get(f"/inventory/{item['id']}", headers=headers)).json()
    types = [tx["transaction_type"] for tx in detail["transactions"]]
    assert "transfer_out" in types and "transfer_in" in types


async def test_transfer_insufficient_source_rejected_atomically(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    main = await _default_location_id(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    await _receive(client, token, item, supplier, 10)  # MAIN = 10

    res = await client.post(
        f"/inventory/{item['id']}/transfer",
        json={"from_location_id": main, "to_location_id": wh2["id"], "quantity": "50"},
        headers=headers,
    )
    assert res.status_code == 409
    # Nothing moved: MAIN still 10, WH2 never created a balance.
    detail = (await client.get(f"/inventory/{item['id']}", headers=headers)).json()
    by_code = {lq["location_code"]: _dec(lq["quantity_on_hand"]) for lq in detail["by_location"]}
    assert by_code == {"MAIN": Decimal("10")}


async def test_transfer_same_location_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    main = await _default_location_id(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/transfer",
        json={"from_location_id": main, "to_location_id": main, "quantity": "5"},
        headers=headers,
    )
    assert res.status_code == 400


# --------------------------------------------------------------------------- #
# MRP + isolation + RBAC
# --------------------------------------------------------------------------- #
async def test_mrp_sums_on_hand_across_locations(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()

    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-06-01", "quantity": "100"},
        headers=headers,
    )
    await _receive(client, token, item, supplier, 30)  # MAIN
    await _receive(client, token, item, supplier, 20, location_id=wh2["id"])  # WH2

    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert _dec(line["on_hand"]) == Decimal("50")  # 30 + 20 across locations
    assert _dec(line["net_requirement"]) == Decimal("50")


async def test_location_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    item_b = await _create_item(client, token_b)
    main_a = await _default_location_id(client, token_a)

    # Org B can't consume against Org A's location.
    res = await client.post(
        f"/inventory/{item_b['id']}/consume",
        json={"quantity": "1", "location_id": main_a},
        headers=headers_b,
    )
    assert res.status_code == 404  # location not found for this org


async def test_rbac_blocks_non_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

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
    member = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.post("/locations", json={"code": "X", "name": "X"}, headers=member)).status_code == 403
    main = await _default_location_id(client, token)
    wh2 = (await _create_location(client, token, "WH2")).json()
    assert (
        await client.post(
            f"/inventory/{item['id']}/transfer",
            json={"from_location_id": main, "to_location_id": wh2["id"], "quantity": "1"},
            headers=member,
        )
    ).status_code == 403
    # Reads are allowed.
    assert (await client.get("/locations", headers=member)).status_code == 200
