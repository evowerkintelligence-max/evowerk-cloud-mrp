from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests._planning_grid_helpers import (
    _register_and_login,
    _create_item,
    _plan,
    _grid,
    _product,
    _today,
    _create_customer,
    _soon,
    _create_order,
)

pytestmark = pytest.mark.asyncio


async def test_committed_quantity_appears_on_the_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=40, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("40")


async def test_a_product_with_only_committed_demand_still_gets_a_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    grid = await _grid(client, token)
    row = _product(grid, "WIDGET-1")
    assert Decimal(row["planned_qty"]) == Decimal(0), "nothing was typed"
    assert Decimal(row["committed_qty"]) == Decimal("15")
    assert row["materials_status"] == "missing", "an order with no stock must show as short"


async def test_committed_quantity_is_zero_without_orders(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)


async def test_a_cancelled_order_leaves_nothing_committed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)
    order = await _create_order(client, token, customer, item, qty=40, needed_by=_soon())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal(0)
    assert Decimal(row["planned_qty"]) == Decimal("10")


async def test_a_committed_only_row_gets_a_ready_by_date(client):
    """A row that exists only because a customer ordered it still needs a
    date: a blank ready-by on a red row is the least useful answer here."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1", lead_time_days=5)
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("15")
    assert row["ready_by"] is not None


async def test_a_committed_product_is_not_offered_a_history_suggestion(client):
    """An order is better information than a forecast guess, so a product a
    customer has already ordered must not be offered one."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=15, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["suggested_qty"] is None


async def test_an_overdue_commitment_is_marked_on_the_row(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    yesterday = str(_today() - timedelta(days=1))
    await _create_order(client, token, customer, item, qty=12, needed_by=yesterday)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert Decimal(row["committed_qty"]) == Decimal("12"), "a late order is still owed"
    assert row["committed_overdue"] is True


async def test_an_on_time_commitment_is_not_marked_overdue(client):
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=12, needed_by=_soon())

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["committed_overdue"] is False


async def test_a_product_with_no_orders_is_not_marked_overdue(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, item, 10)

    row = _product(await _grid(client, token), "WIDGET-1")
    assert row["committed_overdue"] is False
