from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


async def _create_item(client, token, sku, name="Item", min_order_qty=None):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals", lead_time_days=None):
    res = await client.post(
        "/suppliers",
        json={"name": name, "lead_time_days": lead_time_days},
        headers={"Authorization": f"Bearer {token}"},
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


async def _create_active_bom(client, token, produced_item, lines):
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    bom_data = bom.json()
    bom_id = bom_data["id"]
    await client.patch(
        f"/boms/{bom_id}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": str(qty), "sequence": i}
                for i, (comp, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_id}/activate", headers=headers)
    assert activate.status_code == 200


async def _generate_po(client, token, supplier, item_ids):
    return await client.post(
        "/mrp/generate-po",
        json={"supplier_id": supplier["id"], "item_ids": item_ids, **HORIZON},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_generate_po_creates_draft_at_net_quantities(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 100)

    res = await _generate_po(client, token, supplier, [item["id"]])
    assert res.status_code == 200
    body = res.json()
    assert body["supplier_id"] == supplier["id"]
    assert len(body["lines"]) == 1
    assert body["lines"][0]["item_id"] == item["id"]
    assert _dec(body["lines"][0]["quantity_ordered"]) == Decimal("100")

    # A real draft PO now exists, visible through the normal PO API.
    po = await client.get(f"/purchase-orders/{body['po_id']}", headers=headers)
    assert po.status_code == 200
    assert po.json()["status"] == "draft"
    assert _dec(po.json()["lines"][0]["quantity_ordered"]) == Decimal("100")


async def test_generate_po_rounds_up_to_moq(client):
    """The PO line quantity must be the MOQ-rounded suggested_order_quantity,
    not the raw net_requirement - otherwise a supplier's MOQ never reaches an
    actual order."""
    token = await _register_and_login(client)
    item = await _create_item(client, token, "ROD", "Steel Rod", min_order_qty=500)
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 20)  # well under the 500 MOQ

    res = await _generate_po(client, token, supplier, [item["id"]])
    assert res.status_code == 200
    lines = res.json()["lines"]
    assert len(lines) == 1
    assert _dec(lines[0]["quantity_ordered"]) == Decimal("500")


async def test_generate_po_prefills_last_known_unit_price(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)

    # A prior PO line establishes a last-known price of 7.50 for this item.
    prior = await client.post(
        "/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers
    )
    await client.patch(
        f"/purchase-orders/{prior.json()['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "7.50", "sequence": 0}],
        headers=headers,
    )

    await _forecast(client, token, item, 100)
    res = await _generate_po(client, token, supplier, [item["id"]])
    assert _dec(res.json()["lines"][0]["unit_price"]) == Decimal("7.50")


async def test_generate_po_defaults_price_to_zero_when_never_purchased(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 10)

    res = await _generate_po(client, token, supplier, [item["id"]])
    assert _dec(res.json()["lines"][0]["unit_price"]) == Decimal("0")


async def test_generate_po_rejects_manufactured_item(client):
    token = await _register_and_login(client)
    fg = await _create_item(client, token, "FG", "Widget")
    rod = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _create_active_bom(client, token, fg, [(rod, 2)])
    await _forecast(client, token, fg, 100)

    # FG has a net requirement but is manufactured - you build it, not buy it.
    res = await _generate_po(client, token, supplier, [fg["id"]])
    assert res.status_code == 400

    # Its exploded component ROD is orderable.
    ok = await _generate_po(client, token, supplier, [rod["id"]])
    assert ok.status_code == 200
    assert _dec(ok.json()["lines"][0]["quantity_ordered"]) == Decimal("200")


async def test_generate_po_rejects_item_with_no_net_requirement(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "ROD", "Steel Rod")
    other = await _create_item(client, token, "BOLT", "Bolt")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 100)  # only ROD has demand

    res = await _generate_po(client, token, supplier, [other["id"]])
    assert res.status_code == 400


async def test_generate_po_empty_selection_rejected(client):
    token = await _register_and_login(client)
    supplier = await _create_supplier(client, token)
    res = await _generate_po(client, token, supplier, [])
    assert res.status_code == 400


async def test_generate_po_unknown_supplier_404(client):
    import uuid

    token = await _register_and_login(client)
    item = await _create_item(client, token, "ROD")
    await _forecast(client, token, item, 10)
    res = await client.post(
        "/mrp/generate-po",
        json={"supplier_id": str(uuid.uuid4()), "item_ids": [item["id"]], **HORIZON},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_generate_po_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")

    item_a = await _create_item(client, token_a, "ROD")
    await _forecast(client, token_a, item_a, 100)
    supplier_b = await _create_supplier(client, token_b, name="SupplierB")

    # Org B can't order Org A's item (it isn't in B's plan) even via B's supplier.
    res = await _generate_po(client, token_b, supplier_b, [item_a["id"]])
    assert res.status_code == 400


async def test_generate_po_requires_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 100)
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
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post(
        "/mrp/generate-po",
        json={"supplier_id": supplier["id"], "item_ids": [item["id"]], **HORIZON},
        headers=member_headers,
    )
    assert res.status_code == 403


async def test_generating_a_draft_prevents_double_ordering(client):
    """Regression: a generated draft counts as on_draft supply, so it offsets
    the net requirement immediately - re-generating (manual or auto) can't
    re-order the same shortfall while the draft sits open."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 100)

    first = await _generate_po(client, token, supplier, [item["id"]])
    assert first.status_code == 200
    assert _dec(first.json()["lines"][0]["quantity_ordered"]) == Decimal("100")

    # The draft now shows as on_draft and the requirement is covered.
    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert _dec(line["on_draft"]) == Decimal("100")
    assert _dec(line["net_requirement"]) == Decimal("0")

    # Re-ordering the same item is now rejected (no net requirement left).
    second = await _generate_po(client, token, supplier, [item["id"]])
    assert second.status_code == 400


async def test_auto_generate_is_idempotent_while_draft_open(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token)
    # Give the item a preferred supplier so auto-generation routes it.
    await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": supplier["id"]},
        headers=headers,
    )
    await _forecast(client, token, item, 100)

    first = await client.post(
        "/mrp/generate-pos",
        json={"horizon_start": "2030-01-01", "horizon_end": "2030-12-31"},
        headers=headers,
    )
    assert len(first.json()["created"]) == 1

    # Second auto-run creates nothing - the open draft already covers it.
    second = await client.post(
        "/mrp/generate-pos",
        json={"horizon_start": "2030-01-01", "horizon_end": "2030-12-31"},
        headers=headers,
    )
    assert second.json() == {"created": [], "unassigned": []}


async def test_generated_po_becomes_on_order_after_submit(client):
    """Submitting the generated draft turns it into on-order supply, so the next
    plan run shows the requirement covered - the loop closes."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD")
    supplier = await _create_supplier(client, token)
    await _forecast(client, token, item, 100)

    gen = await _generate_po(client, token, supplier, [item["id"]])
    po_id = gen.json()["po_id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert _dec(line["on_order"]) == Decimal("100")
    assert _dec(line["net_requirement"]) == Decimal("0")


async def test_generate_po_defaults_expected_delivery_date_from_supplier_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token, lead_time_days=14)
    await _forecast(client, token, item, 100)

    res = await _generate_po(client, token, supplier, [item["id"]])
    assert res.status_code == 200

    po = await client.get(f"/purchase-orders/{res.json()['po_id']}", headers=headers)
    expected = (datetime.now(timezone.utc).date() + timedelta(days=14)).isoformat()
    assert po.json()["expected_delivery_date"] == expected
