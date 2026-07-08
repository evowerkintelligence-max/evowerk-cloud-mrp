from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


def _dec(v) -> Decimal:
    # Numeric(12, 4) values come back scaled ("120.0000") when DB-round-tripped
    # but bare ("120") when freshly written or computed in Python. That trailing-
    # zero difference is a serialization artifact, not behaviour - compare the
    # numeric value, not its string form.
    return Decimal(str(v))

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


async def _create_po(client, token, item, supplier, qty="100", submit=True):
    """Returns (po_id, line_id). submit=False leaves it in draft."""
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": qty, "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    if submit:
        await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    return po_id, line_id


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def test_manual_upsert_creates_then_overwrites(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)

    create = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2026-08-01", "quantity": "120"},
        headers=headers,
    )
    assert create.status_code == 200
    assert _dec(create.json()["quantity"]) == Decimal("120")
    assert create.json()["source"] == "manual"

    # Same item + period overwrites (set, not add) rather than duplicating.
    update = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2026-08-01", "quantity": "80", "notes": "revised"},
        headers=headers,
    )
    assert update.status_code == 200
    assert _dec(update.json()["quantity"]) == Decimal("80")
    assert update.json()["notes"] == "revised"

    listing = await client.get("/forecasts", headers=headers)
    assert len(listing.json()) == 1
    assert _dec(listing.json()[0]["quantity"]) == Decimal("80")


async def test_manual_upsert_rejects_negative_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2026-08-01", "quantity": "-5"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_upsert_unknown_item_404(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    import uuid

    res = await client.post(
        "/forecasts",
        json={"item_id": str(uuid.uuid4()), "period_start": "2026-08-01", "quantity": "5"},
        headers=headers,
    )
    assert res.status_code == 404


async def test_bulk_import_creates_and_reimport_updates(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)

    first = await client.post(
        "/forecasts/import",
        json=[
            {"item_id": item["id"], "period_start": "2026-08-01", "quantity": "100"},
            {"item_id": item["id"], "period_start": "2026-09-01", "quantity": "150"},
        ],
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json() == {"created": 2, "updated": 0, "total": 2}

    # Re-import: one existing period (overwrite), one new (create).
    second = await client.post(
        "/forecasts/import",
        json=[
            {"item_id": item["id"], "period_start": "2026-08-01", "quantity": "110"},
            {"item_id": item["id"], "period_start": "2026-10-01", "quantity": "200"},
        ],
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json() == {"created": 1, "updated": 1, "total": 2}

    listing = await client.get("/forecasts", headers=headers)
    by_period = {row["period_start"]: row for row in listing.json()}
    assert by_period["2026-08-01"]["quantity"] == "110.0000"
    assert by_period["2026-08-01"]["source"] == "imported"
    assert len(listing.json()) == 3  # aug, sep, oct


async def test_bulk_import_rejects_whole_batch_on_bad_row(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    import uuid

    res = await client.post(
        "/forecasts/import",
        json=[
            {"item_id": item["id"], "period_start": "2026-08-01", "quantity": "100"},
            {"item_id": str(uuid.uuid4()), "period_start": "2026-09-01", "quantity": "150"},
        ],
        headers=headers,
    )
    assert res.status_code == 404
    # Nothing was written - the good row must not have leaked through.
    listing = await client.get("/forecasts", headers=headers)
    assert listing.json() == []


async def test_delete_forecast(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    created = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2026-08-01", "quantity": "10"},
        headers=headers,
    )
    fc_id = created.json()["id"]
    delete = await client.delete(f"/forecasts/{fc_id}", headers=headers)
    assert delete.status_code == 204
    assert (await client.get("/forecasts", headers=headers)).json() == []
    # Deleting again is a 404.
    assert (await client.delete(f"/forecasts/{fc_id}", headers=headers)).status_code == 404


# --------------------------------------------------------------------------- #
# MRP netting
# --------------------------------------------------------------------------- #
async def test_mrp_plan_nets_demand_against_onhand_and_onorder(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)

    # Demand 100 in the horizon.
    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-01-15", "quantity": "100"},
        headers=headers,
    )

    # on_hand = 30: order 30, submit, receive all into an active lot.
    recv_po, recv_line = await _create_po(client, token, item, supplier, qty="30")
    await client.post(
        f"/purchase-orders/{recv_po}/receive",
        json=[{"po_line_id": recv_line, "quantity": "30", "lot_number": "LOT-1"}],
        headers=headers,
    )

    # on_order = 20: order 20, submit, leave unreceived.
    await _create_po(client, token, item, supplier, qty="20")

    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    assert plan.status_code == 200
    lines = plan.json()["lines"]
    assert len(lines) == 1
    line = lines[0]
    assert _dec(line["gross_demand"]) == Decimal("100")
    assert _dec(line["on_hand"]) == Decimal("30")
    assert _dec(line["on_order"]) == Decimal("20")
    assert _dec(line["net_requirement"]) == Decimal("50")
    assert _dec(line["suggested_order_quantity"]) == Decimal("50")
    assert line["on_hand_tracked"] is True

    # Putting the lot on hold removes it from available on-hand -> shortfall grows.
    lot_id = (await client.get("/lots", headers=headers)).json()[0]["id"]
    await client.post(f"/lots/{lot_id}/hold", headers=headers)
    plan2 = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line2 = plan2.json()["lines"][0]
    assert _dec(line2["on_hand"]) == Decimal("0")
    assert _dec(line2["net_requirement"]) == Decimal("80")


async def test_mrp_plan_ignores_draft_po_and_out_of_horizon_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    supplier = await _create_supplier(client, token)

    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-06-01", "quantity": "50"},
        headers=headers,
    )
    # Demand outside the horizon must not be counted.
    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2031-06-01", "quantity": "999"},
        headers=headers,
    )
    # A draft (never submitted) PO is not committed supply.
    await _create_po(client, token, item, supplier, qty="40", submit=False)

    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert _dec(line["gross_demand"]) == Decimal("50")  # 999 excluded
    assert _dec(line["on_order"]) == Decimal("0")  # draft excluded
    assert _dec(line["net_requirement"]) == Decimal("50")


async def test_mrp_plan_non_lot_tracked_item_reports_untracked_onhand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=False)

    await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-03-01", "quantity": "70"},
        headers=headers,
    )
    plan = await client.get(
        "/mrp/plan?horizon_start=2030-01-01&horizon_end=2030-12-31", headers=headers
    )
    line = plan.json()["lines"][0]
    assert line["on_hand_tracked"] is False
    assert _dec(line["on_hand"]) == Decimal("0")
    assert _dec(line["net_requirement"]) == Decimal("70")


async def test_mrp_plan_rejects_inverted_horizon(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(
        "/mrp/plan?horizon_start=2030-12-31&horizon_end=2030-01-01", headers=headers
    )
    assert res.status_code == 400


async def test_mrp_plan_empty_when_no_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    plan = await client.get("/mrp/plan", headers=headers)
    assert plan.status_code == 200
    assert plan.json()["lines"] == []


# --------------------------------------------------------------------------- #
# Isolation + RBAC
# --------------------------------------------------------------------------- #
async def test_forecast_cross_tenant_isolation(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    item_a = await _create_item(client, token_a)
    await client.post(
        "/forecasts",
        json={"item_id": item_a["id"], "period_start": "2030-01-01", "quantity": "10"},
        headers=headers_a,
    )

    assert (await client.get("/forecasts", headers=headers_b)).json() == []
    assert (await client.get("/mrp/plan", headers=headers_b)).json()["lines"] == []

    # Org B cannot forecast against Org A's item.
    res = await client.post(
        "/forecasts",
        json={"item_id": item_a["id"], "period_start": "2030-01-01", "quantity": "5"},
        headers=headers_b,
    )
    assert res.status_code == 404


async def test_rbac_blocks_non_owner_from_forecast_writes(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    item = await _create_item(client, token)
    created = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2030-01-01", "quantity": "10"},
        headers=headers,
    )
    fc_id = created.json()["id"]

    # register() only makes owners, so a non-owner is constructed directly
    # (same rationale as the lot/bom/procurement RBAC tests).
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

    # Writes are owner-only.
    assert (
        await client.post(
            "/forecasts",
            json={"item_id": item["id"], "period_start": "2030-02-01", "quantity": "1"},
            headers=member_headers,
        )
    ).status_code == 403
    assert (
        await client.post(
            "/forecasts/import",
            json=[{"item_id": item["id"], "period_start": "2030-02-01", "quantity": "1"}],
            headers=member_headers,
        )
    ).status_code == 403
    assert (await client.delete(f"/forecasts/{fc_id}", headers=member_headers)).status_code == 403

    # Reads are allowed for any member.
    assert (await client.get("/forecasts", headers=member_headers)).status_code == 200
    assert (await client.get("/mrp/plan", headers=member_headers)).status_code == 200
