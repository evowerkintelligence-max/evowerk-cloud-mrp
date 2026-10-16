from __future__ import annotations

from decimal import Decimal

import pytest

from tests._sales_orders_helpers import (
    _register_and_login,
    _create_customer,
    _create_item,
    _create_order,
    _give_stock,
    _soon,
    _create_active_bom,
    _plan_lines,
    _yesterday,
)

pytestmark = pytest.mark.asyncio


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


async def test_an_overdue_open_order_still_counts_as_demand(client):
    """A forecast bucket in the past is spent; an open order in the past is
    not, it is late. Dropping it would turn the grid green for the single most
    at-risk commitment in the shop."""
    token = await _register_and_login(client)
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    await _create_order(client, token, customer, item, qty=25, needed_by=_yesterday())

    lines = await _plan_lines(client, token)
    assert Decimal(lines["WIDGET-1"]["net_requirement"]) == Decimal("25")


async def test_an_overdue_order_is_flagged_late(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    late = await _create_order(client, token, customer, item, qty=5, needed_by=_yesterday())
    on_time = await _create_order(client, token, customer, item, qty=5, needed_by=_soon())

    assert late["is_overdue"] is True
    assert on_time["is_overdue"] is False

    by_id = {o["id"]: o for o in (await client.get("/sales-orders", headers=headers)).json()}
    assert by_id[late["id"]]["is_overdue"] is True
    assert by_id[on_time["id"]]["is_overdue"] is False


async def test_a_closed_overdue_order_is_not_flagged_late(client):
    """Late only means "still owed": cancelling or completing settles it."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    item = await _create_item(client, token, "WIDGET-1")
    order = await _create_order(client, token, customer, item, qty=5, needed_by=_yesterday())
    await client.post(f"/sales-orders/{order['id']}/cancel", headers=headers)

    fetched = await client.get(f"/sales-orders/{order['id']}", headers=headers)
    assert fetched.json()["is_overdue"] is False
    assert await _plan_lines(client, token) == {}, "a cancelled order is settled, late or not"
