from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


async def _create_item(client, token, sku="RAW-1", name="Steel Rod"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name, "email": "sales@acmemetals.example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def test_supplier_create_and_duplicate_name_rejected(client):
    token = await _register_and_login(client)
    await _create_supplier(client, token, name="DupeCo")

    res = await client.post(
        "/suppliers", json={"name": "DupeCo"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 409


async def test_po_full_lifecycle(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    assert po.status_code == 201
    po_data = po.json()
    assert po_data["status"] == "draft"
    assert po_data["po_number"] == 1

    lines = await client.patch(
        f"/purchase-orders/{po_data['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "100", "unit_price": "2.50", "sequence": 0}],
        headers=headers,
    )
    assert lines.status_code == 200
    line_id = lines.json()["lines"][0]["id"]

    submit = await client.post(f"/purchase-orders/{po_data['id']}/submit", headers=headers)
    assert submit.status_code == 200
    assert submit.json()["status"] == "submitted"

    # Draft-only edit guard: a submitted PO's lines can no longer be edited.
    edit_after_submit = await client.patch(
        f"/purchase-orders/{po_data['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "1", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    assert edit_after_submit.status_code == 409

    partial_receive = await client.post(
        f"/purchase-orders/{po_data['id']}/receive",
        json=[{"po_line_id": line_id, "quantity": "40"}],
        headers=headers,
    )
    assert partial_receive.status_code == 200
    assert partial_receive.json()["status"] == "partially_received"
    assert partial_receive.json()["lines"][0]["quantity_received"] == "40.0000"

    full_receive = await client.post(
        f"/purchase-orders/{po_data['id']}/receive",
        json=[{"po_line_id": line_id, "quantity": "60"}],
        headers=headers,
    )
    assert full_receive.status_code == 200
    assert full_receive.json()["status"] == "received"


async def test_over_receipt_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    over = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "11"}],
        headers=headers,
    )
    assert over.status_code == 409


async def test_over_receipt_rejected_across_duplicate_lines_in_one_request(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    # Two receipts against the same line in one request, each individually
    # within bounds but exceeding quantity_ordered combined.
    over = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {"po_line_id": line_id, "quantity": "6"},
            {"po_line_id": line_id, "quantity": "6"},
        ],
        headers=headers,
    )
    assert over.status_code == 409

    # Nothing was applied - the line's quantity_received is untouched.
    detail = await client.get(f"/purchase-orders/{po_id}", headers=headers)
    assert detail.json()["lines"][0]["quantity_received"] == "0.0000"


async def test_cancel_allowed_before_receiving_blocked_after(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    # Cancel from draft: allowed.
    po1 = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    cancel1 = await client.post(f"/purchase-orders/{po1.json()['id']}/cancel", headers=headers)
    assert cancel1.status_code == 200
    assert cancel1.json()["status"] == "cancelled"

    # Cancel from submitted: allowed.
    po2 = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po2_id = po2.json()["id"]
    await client.patch(
        f"/purchase-orders/{po2_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    await client.post(f"/purchase-orders/{po2_id}/submit", headers=headers)
    cancel2 = await client.post(f"/purchase-orders/{po2_id}/cancel", headers=headers)
    assert cancel2.status_code == 200

    # Cancel once any receiving has happened: blocked.
    po3 = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po3_id = po3.json()["id"]
    lines3 = await client.patch(
        f"/purchase-orders/{po3_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line3_id = lines3.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po3_id}/submit", headers=headers)
    await client.post(
        f"/purchase-orders/{po3_id}/receive",
        json=[{"po_line_id": line3_id, "quantity": "1"}],
        headers=headers,
    )
    cancel3 = await client.post(f"/purchase-orders/{po3_id}/cancel", headers=headers)
    assert cancel3.status_code == 409


async def test_procurement_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")

    supplier_a = await _create_supplier(client, token_a, name="OrgA Supplier")
    po_a = await client.post(
        "/purchase-orders", json={"supplier_id": supplier_a["id"]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    po_a_id = po_a.json()["id"]

    suppliers_b = await client.get("/suppliers", headers={"Authorization": f"Bearer {token_b}"})
    assert all(s["id"] != supplier_a["id"] for s in suppliers_b.json())

    get_po_as_b = await client.get(
        f"/purchase-orders/{po_a_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert get_po_as_b.status_code == 404


async def test_rbac_blocks_non_owner_from_procurement_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    org_id = me.json()["id"]

    # Same rationale as test_bom.py's RBAC test: register() always creates
    # "owner" users, so a non-owner has to be constructed directly.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        member = User(
            org_id=uuid_module.UUID(org_id),
            email="member2@example.com",
            password_hash=hash_password(PASSWORD),
            role="member",
            is_active=True,
        )
        session.add(member)
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_token = login.json()["access_token"]

    res = await client.post(
        "/suppliers", json={"name": "Blocked Co"}, headers={"Authorization": f"Bearer {member_token}"}
    )
    assert res.status_code == 403

    read = await client.get("/suppliers", headers={"Authorization": f"Bearer {member_token}"})
    assert read.status_code == 200


async def test_po_number_allocates_sequentially(client):
    """Repeated creates for the same org get distinct, sequential po_numbers.

    This doesn't exercise the IntegrityError retry guard itself - the
    in-memory SQLite test DB (a single StaticPool connection, see
    conftest.py) can't safely run truly concurrent transactions the way a
    real Postgres connection pool can, so a genuine race can't be simulated
    here. The retry guard mirrors create_bom's identical, already-shipped
    pattern; this test covers the happy path it falls back to.
    """
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    numbers = []
    for _ in range(5):
        res = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
        assert res.status_code == 201
        numbers.append(res.json()["po_number"])

    assert numbers == [1, 2, 3, 4, 5]


async def test_receive_with_rejected_quantity_only_applies_accepted_to_inventory(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "10",
                "quantity_rejected": "3",
                "reject_reason": "damaged",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    # The full quantity (accepted + rejected) still closes out the line.
    assert receive.json()["lines"][0]["quantity_received"] == "10.0000"
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    # Only the accepted 7 units landed in inventory, not the full 10.
    assert inventory.json()["quantity_on_hand"] == "7.0000"


async def test_receive_fully_rejected_applies_no_inventory_transaction(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "5",
                "quantity_rejected": "5",
                "reject_reason": "wrong spec",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    assert inventory.json()["quantity_on_hand"] == "0.0000"


async def test_receive_rejects_quantity_rejected_exceeding_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "5", "quantity_rejected": "6"}],
        headers=headers,
    )
    assert receive.status_code == 400


async def test_create_po_defaults_expected_delivery_date_from_supplier_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    supplier = (
        await client.post(
            "/suppliers", json={"name": "Acme Metals", "lead_time_days": 14}, headers=headers
        )
    ).json()

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    assert po.status_code == 201
    expected = (datetime.now(timezone.utc).date() + timedelta(days=14)).isoformat()
    assert po.json()["expected_delivery_date"] == expected


async def test_create_po_leaves_expected_delivery_date_null_without_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)  # no lead_time_days set

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    assert po.status_code == 201
    assert po.json()["expected_delivery_date"] is None


async def test_create_po_explicit_expected_delivery_date_overrides_lead_time_default(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = (
        await client.post(
            "/suppliers", json={"name": "Acme Metals", "lead_time_days": 14}, headers=headers
        )
    ).json()

    po = await client.post(
        "/purchase-orders",
        json={"supplier_id": supplier["id"], "expected_delivery_date": "2026-12-25"},
        headers=headers,
    )
    assert po.status_code == 201
    assert po.json()["expected_delivery_date"] == "2026-12-25"
