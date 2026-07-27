from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_item(client, token, sku, name="Widget", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "finished_good",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_order(client, token, customer, item, qty=10, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_new_org_defaults_to_consuming_demand(client):
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["demand_policy"] == "consume"


async def test_demand_policy_can_be_set_to_add(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.patch("/orgs/me", json={"demand_policy": "add"}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["demand_policy"] == "add"
    assert (await client.get("/orgs/me", headers=headers)).json()["demand_policy"] == "add"


async def test_unknown_demand_policy_is_rejected(client):
    token = await _register_and_login(client)
    res = await client.patch(
        "/orgs/me", json={"demand_policy": "whatever"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400


async def test_order_numbers_allocate_sequentially_per_org(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")

    numbers = [
        (await _create_order(client, token, customer, item))["so_number"] for _ in range(3)
    ]
    assert numbers == [1, 2, 3]


async def test_order_requires_at_least_one_line(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    res = await client.post(
        "/sales-orders",
        json={"customer_id": customer["id"], "needed_by": "2030-06-01", "lines": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_a_nonpositive_quantity(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "0", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_order_rejects_an_unknown_customer(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": "00000000-0000-0000-0000-000000000000",
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "5", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_cancelling_an_order_sets_cancelled(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item)

    res = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    again = await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)
    assert again.status_code == 409


async def test_orders_are_org_scoped(client):
    token_a = await _register_and_login(client)
    customer = await _create_customer(client, token_a)
    item = await _create_item(client, token_a, "WIDGET-1")
    order = await _create_order(client, token_a, customer, item)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/sales-orders", headers=headers_b)).json() == []
    assert (await client.get(f"/sales-orders/{order['id']}", headers=headers_b)).status_code == 404


async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _receive_into_lot(client, token, item, qty, lot_number):
    """Gives a lot-tracked item real on-hand stock via a received PO lot. Lot
    stock cannot be seeded any other way, so this needs a supplier too."""
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post(
        "/suppliers", json={"name": f"Acme Metals {lot_number}"}, headers=headers
    )
    assert supplier.status_code == 201, supplier.text
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    recv = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}],
        headers=headers,
    )
    assert recv.status_code == 200, recv.text


async def test_completing_an_order_removes_the_stock(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=20)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert body["lines"][0]["quantity_delivered"] == "20.0000"

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("30")


async def test_completing_without_enough_stock_is_refused_and_moves_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 5)
    order = await _create_order(client, token, customer, item, qty=20)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 409

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("5"), "a refused completion must move no stock"
    still = await client.get(f"/sales-orders/{order['id']}", headers=headers)
    assert still.json()["status"] == "open", "a refused completion must leave the order open"


async def test_completing_twice_is_refused(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=10)

    assert (await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)).status_code == 200
    again = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert again.status_code == 409

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("40"), "stock must come off once, not twice"


async def test_a_cancelled_order_cannot_be_completed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=10)
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 409


async def test_completing_a_lot_tracked_order_draws_from_lots(client):
    """A lot-tracked finished good cannot go through apply_inventory_delta at
    all, so this pins the other branch."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "TRACKED-1", is_lot_tracked=True)

    # Lot-tracked stock only enters through a received purchase order.
    await _receive_into_lot(client, token, item, qty=50, lot_number="LOT-A")

    order = await _create_order(client, token, customer, item, qty=20)
    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 200, res.text

    lots = await client.get("/lots", headers=headers)
    remaining = sum(Decimal(lot["quantity_remaining"]) for lot in lots.json())
    assert remaining == Decimal("30")


def _soon() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per). Creates a draft BOM, sets its
    lines, and activates it."""
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    patch = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": component["id"],
                    "quantity": str(qty),
                    "sequence": i,
                    "scrap_pct": "0",
                }
                for i, (component, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200
    return bom_data["id"]


async def _plan_lines(client, token):
    res = await client.get("/mrp/plan", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return {line["item_sku"]: line for line in res.json()["lines"]}


async def test_an_open_order_alone_produces_a_plan(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=25, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("25"), (
        "an order with no forecast must still plan"
    )


async def test_a_cancelled_order_is_not_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item, qty=25, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    assert await _plan_lines(client, token) == {}


async def test_a_completed_order_is_not_demand(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 100)
    order = await _create_order(client, token, customer, item, qty=25, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)

    assert await _plan_lines(client, token) == {}


async def test_consume_policy_does_not_double_count(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": "100", "needed_by": _soon()},
        headers=headers,
    )
    await _create_order(client, token, customer, item, qty=60, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("100"), (
        "60 ordered against a 100 estimate must plan 100, not 160"
    )


async def test_add_policy_sums_both_sources(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch("/orgs/me", json={"demand_policy": "add"}, headers=headers)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": "100", "needed_by": _soon()},
        headers=headers,
    )
    await _create_order(client, token, customer, item, qty=60, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("160")


async def test_orders_explode_through_a_bom(client):
    """An order for a built product must generate component demand, proving
    committed demand flows through the same explosion forecasts do."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _create_order(client, token, customer, pump, qty=10, needed_by=_soon())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20")


async def test_order_rejects_a_quantity_beyond_the_column_range(client):
    """Numeric(12, 4) overflows on Postgres, which would be a 500 rather than
    a message the user can act on. SQLite does not enforce precision, so only
    this boundary check catches it."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "100000000", "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def _create_two_line_order(client, token, customer, first, second, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [
                {"item_id": first[0]["id"], "quantity_ordered": str(first[1]), "sequence": 0},
                {"item_id": second[0]["id"], "quantity_ordered": str(second[1]), "sequence": 1},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_a_shortfall_on_one_line_rolls_back_the_other_lines_stock(client):
    """The whole completion is one transaction, so a line that cannot be
    covered must undo the stock the earlier lines already moved. A single-line
    order cannot prove this: there is nothing to roll back."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    plenty = await _create_item(client, token, "PLENTY-1")
    short = await _create_item(client, token, "SHORT-1")
    await _give_stock(client, token, plenty, 100)
    await _give_stock(client, token, short, 1)
    order = await _create_two_line_order(client, token, customer, (plenty, 10), (short, 20))

    res = await client.post(f"/sales-orders/{order['id']}/complete", headers=headers)
    assert res.status_code == 409

    plenty_inv = await client.get(f"/inventory/{plenty['id']}", headers=headers)
    assert Decimal(plenty_inv.json()["quantity_on_hand"]) == Decimal("100"), (
        "the covered line's stock must be put back when a later line is short"
    )
    short_inv = await client.get(f"/inventory/{short['id']}", headers=headers)
    assert Decimal(short_inv.json()["quantity_on_hand"]) == Decimal("1")

    still = await client.get(f"/sales-orders/{order['id']}", headers=headers)
    assert still.json()["status"] == "open"
    assert all(
        Decimal(line["quantity_delivered"]) == Decimal(0) for line in still.json()["lines"]
    ), "no line may record a delivery when the completion was refused"


async def test_editing_an_open_orders_lines_replaces_them(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    first = await _create_item(client, token, "WIDGET-1")
    second = await _create_item(client, token, "WIDGET-2")
    order = await _create_order(client, token, customer, first, qty=10)

    res = await client.patch(
        f"/sales-orders/{order['id']}",
        json=[{"item_id": second["id"], "quantity_ordered": "7", "sequence": 0}],
        headers=headers,
    )
    assert res.status_code == 200, res.text
    lines = res.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["item_sku"] == "WIDGET-2"
    assert Decimal(lines[0]["quantity_ordered"]) == Decimal("7")


async def test_editing_rejects_an_empty_or_nonpositive_line_set(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item)

    empty = await client.patch(f"/sales-orders/{order['id']}", json=[], headers=headers)
    assert empty.status_code == 400
    zero = await client.patch(
        f"/sales-orders/{order['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "0", "sequence": 0}],
        headers=headers,
    )
    assert zero.status_code == 400

    # The refused edits left the original line alone.
    lines = (await client.get(f"/sales-orders/{order['id']}", headers=headers)).json()["lines"]
    assert Decimal(lines[0]["quantity_ordered"]) == Decimal("10")


async def test_a_closed_order_cannot_be_edited(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item)
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    res = await client.patch(
        f"/sales-orders/{order['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": "3", "sequence": 0}],
        headers=headers,
    )
    assert res.status_code == 409


async def test_listing_filters_by_status_and_searches(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    northwind = await _create_customer(client, token)
    contoso = await _create_customer(client, token, name="Contoso")
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, northwind, item)
    cancelled = await _create_order(client, token, contoso, item)
    await client.post(f"/sales-orders/{cancelled['id']}/cancel", headers=headers)

    open_only = await client.get("/sales-orders?status=open", headers=headers)
    assert [o["customer_name"] for o in open_only.json()] == ["Northwind Ltd"]

    by_customer = await client.get("/sales-orders?q=contoso", headers=headers)
    assert [o["customer_name"] for o in by_customer.json()] == ["Contoso"]

    by_number = await client.get("/sales-orders?q=2", headers=headers)
    assert [o["so_number"] for o in by_number.json()] == [2]

    page = await client.get("/sales-orders?limit=1&offset=1", headers=headers)
    assert [o["so_number"] for o in page.json()] == [2]


async def _add_member(test_engine, client, headers):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post(
        "/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_a_member_can_read_orders_but_not_raise_or_complete_them(client, test_engine):
    """Completing an order moves stock, so that a plain member cannot do it is
    worth pinning rather than inferring from the decorator."""
    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _give_stock(client, token, item, 50)
    order = await _create_order(client, token, customer, item, qty=10)

    member_headers = await _add_member(test_engine, client, headers)

    assert (await client.get("/sales-orders", headers=member_headers)).status_code == 200
    raise_attempt = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": "2030-06-01",
            "lines": [{"item_id": item["id"], "quantity_ordered": "1", "sequence": 0}],
        },
        headers=member_headers,
    )
    assert raise_attempt.status_code == 403
    assert (
        await client.post(f"/sales-orders/{order['id']}/complete", headers=member_headers)
    ).status_code == 403
    assert (
        await client.post(f"/sales-orders/{order['id']}/cancel", headers=member_headers)
    ).status_code == 403

    inv = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("50"), "no refused call may move stock"
