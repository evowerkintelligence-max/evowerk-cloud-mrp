from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit
HORIZON = {"horizon_start": "2030-01-01", "horizon_end": "2030-12-31"}


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


async def _create_item(client, token, sku, name="Item", preferred_supplier_id=None):
    payload = {"sku": sku, "name": name, "item_type": "component", "unit_of_measure": "ea"}
    if preferred_supplier_id is not None:
        payload["preferred_supplier_id"] = preferred_supplier_id
    res = await client.post("/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _forecast(client, token, item, qty, period="2030-06-01"):
    res = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": period, "quantity": str(qty)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


async def _generate_pos(client, token):
    return await client.post(
        "/mrp/generate-pos", json=HORIZON, headers={"Authorization": f"Bearer {token}"}
    )


# --------------------------------------------------------------------------- #
# Setting the preferred supplier
# --------------------------------------------------------------------------- #
async def test_create_item_with_preferred_supplier(client):
    token = await _register_and_login(client)
    supplier = await _create_supplier(client, token)
    item = await _create_item(client, token, "ROD", preferred_supplier_id=supplier["id"])
    assert item["preferred_supplier_id"] == supplier["id"]


async def test_patch_sets_and_clears_preferred_supplier(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    item = await _create_item(client, token, "ROD")
    assert item["preferred_supplier_id"] is None

    set_res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": supplier["id"]},
        headers=headers,
    )
    assert set_res.status_code == 200
    assert set_res.json()["preferred_supplier_id"] == supplier["id"]

    clear_res = await client.patch(
        f"/items/{item['id']}", json={"preferred_supplier_id": None}, headers=headers
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["preferred_supplier_id"] is None


async def test_preferred_supplier_must_belong_to_org(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    supplier_b = await _create_supplier(client, token_b, name="SupplierB")

    # Org A can't point an item at Org B's supplier.
    res = await client.post(
        "/items",
        json={"sku": "ROD", "name": "Rod", "preferred_supplier_id": supplier_b["id"]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res.status_code == 404


async def test_is_lot_tracked_immutable(client):
    # is_lot_tracked is fixed at creation and load-bearing: the on-hand
    # source-of-truth split (lots vs inventory_balances) depends on it never
    # changing. PATCH /items only edits preferred_supplier_id, so even a client
    # that sends is_lot_tracked must not be able to flip it. If someone wires
    # is_lot_tracked into ItemUpdateRequest later, this test fails - a deliberate
    # tripwire (see Item.is_lot_tracked).
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD")
    assert item["is_lot_tracked"] is False

    res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": None, "is_lot_tracked": True},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["is_lot_tracked"] is False
    assert (await client.get("/items", headers=headers)).json()[0]["is_lot_tracked"] is False


async def test_patch_item_requires_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    item = await _create_item(client, token, "ROD")
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

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
    res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": supplier["id"]},
        headers=member_headers,
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# Automatic PO generation by preferred supplier
# --------------------------------------------------------------------------- #
async def test_generate_pos_groups_by_preferred_supplier(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    sup1 = await _create_supplier(client, token, name="Alpha Supply")
    sup2 = await _create_supplier(client, token, name="Beta Supply")

    rod = await _create_item(client, token, "ROD", preferred_supplier_id=sup1["id"])
    bolt = await _create_item(client, token, "BOLT", preferred_supplier_id=sup1["id"])
    nut = await _create_item(client, token, "NUT", preferred_supplier_id=sup2["id"])
    await _forecast(client, token, rod, 100)
    await _forecast(client, token, bolt, 50)
    await _forecast(client, token, nut, 30)

    res = await _generate_pos(client, token)
    assert res.status_code == 200
    body = res.json()
    assert body["unassigned"] == []
    # One PO per supplier: sup1 (rod+bolt), sup2 (nut).
    by_supplier = {po["supplier_id"]: po for po in body["created"]}
    assert len(by_supplier) == 2
    assert by_supplier[sup1["id"]]["line_count"] == 2
    assert by_supplier[sup2["id"]]["line_count"] == 1

    # Each is a real draft PO.
    pos = await client.get("/purchase-orders", headers=headers)
    assert {po["status"] for po in pos.json()} == {"draft"}


async def test_generate_pos_reports_unassigned_items(client):
    token = await _register_and_login(client)
    sup = await _create_supplier(client, token)
    with_sup = await _create_item(client, token, "ROD", preferred_supplier_id=sup["id"])
    without = await _create_item(client, token, "BOLT")  # no preferred supplier
    await _forecast(client, token, with_sup, 100)
    await _forecast(client, token, without, 40)

    res = await _generate_pos(client, token)
    body = res.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["line_count"] == 1
    assert len(body["unassigned"]) == 1
    assert body["unassigned"][0]["item_id"] == without["id"]
    assert _dec(body["unassigned"][0]["net_requirement"]) == Decimal("40")


async def test_generate_pos_excludes_manufactured_items(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    sup = await _create_supplier(client, token)
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", preferred_supplier_id=sup["id"])
    # FG (manufactured) -> 2x ROD.
    bom = await client.post("/boms", json={"item_id": fg["id"]}, headers=headers)
    bom_id = bom.json()["id"]
    await client.patch(
        f"/boms/{bom_id}",
        json=[{"component_item_id": rod["id"], "quantity": "2", "sequence": 0}],
        headers=headers,
    )
    await client.post(f"/boms/{bom_id}/activate", headers=headers)
    await _forecast(client, token, fg, 100)

    res = await _generate_pos(client, token)
    body = res.json()
    # FG is manufactured (never ordered); only its exploded component ROD is.
    assert len(body["created"]) == 1
    assert body["created"][0]["line_count"] == 1
    po_id = body["created"][0]["po_id"]
    po = await client.get(f"/purchase-orders/{po_id}", headers=headers)
    assert po.json()["lines"][0]["item_id"] == rod["id"]
    assert _dec(po.json()["lines"][0]["quantity_ordered"]) == Decimal("200")


async def test_generate_pos_empty_when_nothing_orderable(client):
    token = await _register_and_login(client)
    res = await _generate_pos(client, token)
    assert res.status_code == 200
    assert res.json() == {"created": [], "unassigned": []}


async def test_generate_pos_requires_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    sup = await _create_supplier(client, token)
    item = await _create_item(client, token, "ROD", preferred_supplier_id=sup["id"])
    await _forecast(client, token, item, 100)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

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
    res = await client.post("/mrp/generate-pos", json=HORIZON, headers=member_headers)
    assert res.status_code == 403
