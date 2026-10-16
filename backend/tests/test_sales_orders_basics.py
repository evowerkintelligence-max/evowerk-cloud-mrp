from __future__ import annotations

from decimal import Decimal

import pytest

from tests._sales_orders_helpers import (
    _register_and_login,
    _create_customer,
    _create_item,
    _create_order,
    _give_stock,
    _add_member,
)

pytestmark = pytest.mark.asyncio


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
