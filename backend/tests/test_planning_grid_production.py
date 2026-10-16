from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests._planning_grid_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _plan,
    _grid,
    _product,
    _today,
    _create_customer,
    _create_order,
    _raise_job,
    _give_stock,
    _start,
)

pytestmark = pytest.mark.asyncio


async def test_the_summary_counts_jobs_needing_attention(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(
        client, token, pump, qty=10, scheduled_start=str(_today() - timedelta(days=3))
    )

    grid = await _grid(client, token)
    assert grid["summary"]["jobs_needing_attention"] == 1


async def test_the_summary_counts_no_attention_for_a_job_on_schedule(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(
        client, token, pump, qty=10, scheduled_start=str(_today() + timedelta(days=3))
    )

    grid = await _grid(client, token)
    assert grid["summary"]["jobs_needing_attention"] == 0


async def test_the_row_shows_what_is_in_production(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=20)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["in_production"]) == Decimal("20")


async def test_a_row_with_materials_can_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, seal, 100)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is True
    assert row["cannot_make_reason"] is None


async def test_a_row_short_of_materials_cannot_be_made(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False, "offering Make on a job that would 409 is a trap"
    assert row["cannot_make_reason"] is not None
    assert "SEAL-1" in row["cannot_make_reason"], (
        "the reason must name the short component, not just say no"
    )


async def test_a_product_with_no_recipe_cannot_be_made(client):
    token = await _register_and_login(client)
    pump = await _create_item(client, token, "PUMP-1")
    await _plan(client, token, pump, 10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False
    assert row["cannot_make_reason"] == "This product has no active bill of materials."


async def test_a_row_already_covered_by_a_job_cannot_be_made_again(client):
    """Nothing outstanding means nothing to raise, and offering Make would
    quietly double the plan."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False


async def test_stock_held_by_a_scheduled_job_does_not_count_as_available(client):
    """The same reservation rule start applies: material another job has
    already claimed cannot also make this row."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    other = await _create_item(client, token, "PUMP-2")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _create_active_bom(client, token, other, [(seal, 1)])
    await _give_stock(client, token, seal, 10)
    await _plan(client, token, pump, 10)
    assert _product(await _grid(client, token), "PUMP-1")["can_make"] is True

    # A job for the OTHER product, so PUMP-1's own outstanding quantity is
    # untouched and only the reservation can refuse it. Undated and numbered
    # first, so it outranks the undated job Make would raise next and holds all
    # ten seals.
    await _raise_job(client, token, other, qty=10)

    row = _product(await _grid(client, token), "PUMP-1")
    assert row["can_make"] is False, "the other job already holds every seal"
    assert row["cannot_make_reason"] is not None and "SEAL-1" in row["cannot_make_reason"], (
        "the reason must name the component the other job is holding, not leave the user "
        "guessing why an otherwise-plannable row has a dead Make button"
    )


async def test_the_build_calculator_still_needs_a_scheduled_jobs_components(client):
    """The two halves of a job must move together in EVERY engine caller. The
    build calculator used to subtract a scheduled job's supply without seeding
    its component demand, so it reported that a build short of 20 seals needed
    nothing, and build-plan/generate-pos raised no PO for them."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=str(_today() + timedelta(days=5)))

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": pump["id"], "build_quantity": "10"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" in lines, lines
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "the scheduled job's seals are still needed even though its pumps count as supply"
    )


async def test_a_buffered_item_with_no_demand_still_gets_a_line(client):
    """Safety stock is a requirement in its own right. The netting loop already
    explodes it onto the item's components, so dropping the item's own line
    would attribute an order to a row the plan never shows."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1", safety_stock_qty=50)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    # Cover the pump's own demand entirely, so the seal is reached by
    # explosion with zero gross demand and only its buffer to justify a line.
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, pump, 10)

    res = await client.get("/mrp/plan", headers=headers)
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" in lines, f"the buffered item vanished from the plan: {list(lines)}"
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("50")


async def test_a_buffer_already_covered_by_stock_stays_off_the_plan(client):
    """The other side of the same gate: a buffer with nothing to do is noise,
    not a line."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1", safety_stock_qty=50)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan(client, token, pump, 10)
    await _give_stock(client, token, pump, 10)
    await _give_stock(client, token, seal, 200)

    res = await client.get("/mrp/plan", headers=headers)
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert "SEAL-1" not in lines, lines


async def test_a_row_wanted_only_by_customers_can_still_be_made(client):
    """A shop working from sales orders types nothing into the grid, so basing
    the Make quantity on the forecast alone left the firmest demand in the shop
    with no way to build it."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    customer = await _create_customer(client, token)
    await _create_order(client, token, customer, pump, qty=25)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["committed_qty"]) == Decimal("25")
    assert Decimal(row["planned_qty"]) == Decimal(0)
    assert Decimal(row["outstanding_qty"]) == Decimal("25")
    assert row["can_make"] is True


async def test_a_committed_row_already_being_made_offers_nothing_more(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    customer = await _create_customer(client, token)
    await _create_order(client, token, customer, pump, qty=25)
    job = await _raise_job(client, token, pump, qty=25)
    await _start(client, token, job)

    row = _product(await _grid(client, token), "PUMP-1")
    assert Decimal(row["outstanding_qty"]) == Decimal(0)
    assert row["can_make"] is False
