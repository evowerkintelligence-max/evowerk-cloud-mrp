from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import drain_background_tasks
from tests._procurement_helpers import (
    PASSWORD,
    _register_and_login,
    _create_item,
    _create_supplier,
)

pytestmark = pytest.mark.asyncio


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


async def test_po_submit_emails_supplier(client, monkeypatch):
    sent = {}

    async def fake_send_email(to, subject, body):
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr("app.api.routes.purchase_orders.send_email", fake_send_email)

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_data = po.json()
    await client.patch(
        f"/purchase-orders/{po_data['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_data['id']}/submit", headers=headers)
    assert submit.status_code == 200
    await drain_background_tasks()

    assert sent["to"] == "sales@acmemetals.example"
    assert sent["subject"] == f"Purchase Order #{po_data['po_number']}"
    assert item["sku"] in sent["body"]


async def test_po_submit_skips_email_when_supplier_has_no_email(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("send_email should not be called when supplier has no email")

    monkeypatch.setattr("app.api.routes.purchase_orders.send_email", fail_if_called)

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier_res = await client.post(
        "/suppliers", json={"name": "NoEmail Co"}, headers=headers
    )
    supplier = supplier_res.json()

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_data = po.json()
    await client.patch(
        f"/purchase-orders/{po_data['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_data['id']}/submit", headers=headers)
    assert submit.status_code == 200


async def test_po_submit_skips_email_when_supplier_email_is_whitespace(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("send_email should not be called for a whitespace-only email")

    monkeypatch.setattr("app.api.routes.purchase_orders.send_email", fail_if_called)

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier_res = await client.post(
        "/suppliers", json={"name": "WhitespaceEmail Co", "email": "   "}, headers=headers
    )
    supplier = supplier_res.json()

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_data = po.json()
    await client.patch(
        f"/purchase-orders/{po_data['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    submit = await client.post(f"/purchase-orders/{po_data['id']}/submit", headers=headers)
    assert submit.status_code == 200


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

    # Same rationale as test_bom_lifecycle.py's RBAC test: register() always creates
    # "owner" users, so a non-owner has to be constructed directly.
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
