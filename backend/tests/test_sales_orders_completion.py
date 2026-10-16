from __future__ import annotations

from decimal import Decimal

import pytest

from tests._sales_orders_helpers import (
    _register_and_login,
    _create_customer,
    _create_item,
    _create_order,
    _give_stock,
    _receive_into_lot,
    _create_two_line_order,
)

pytestmark = pytest.mark.asyncio


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
