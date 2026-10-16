from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction
from tests._planning_grid_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _plan,
    _grid,
    _product,
    _raise_job,
    _give_stock,
    _start,
)

pytestmark = pytest.mark.asyncio


async def test_detail_carries_the_product_and_its_components(client):
    # Product-first (ZPUMP-1) and alphabetical (ASEAL-1) disagree here, so this
    # only passes if the "product floats to the top" sort key is real.
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "ASEAL-1")
    pump = await _create_item(client, token, "ZPUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    await _plan(client, token, pump, 50)
    grid = await _grid(client, token)

    detail = _product(grid, "ZPUMP-1")["detail"]
    assert [line["item_sku"] for line in detail] == ["ZPUMP-1", "ASEAL-1"]
    assert detail[0]["is_manufactured"] is True
    # Compare the number, not its string form: BOM explosion multiplies through
    # quantity * (1 + scrap_pct/100), so the engine's Decimal carries more
    # places than a DB-round-tripped value would.
    assert Decimal(detail[1]["gross_demand"]) == Decimal("100")
    assert Decimal(detail[1]["net_requirement"]) == Decimal("100")


async def test_detail_shows_safety_stock_and_in_production_so_the_arithmetic_reconciles(client):
    """net_requirement = gross_demand - on_hand - on_order - on_draft -
    in_production + safety_stock (see mrp._explode_and_net). Both
    in_production and safety_stock used to be folded into net_requirement
    with no visible line, so a user checking Needed - In stock - On order
    against Still needed could never make the numbers add up.

    Both apply to the product's own row here: in_production is supply from a
    job already raised for PUMP-1 itself (not one of its components), and
    safety_stock is PUMP-1's own buffer."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1", safety_stock_qty=5)
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)
    await _plan(client, token, pump, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    detail = _product(await _grid(client, token), "PUMP-1")["detail"]
    pump_line = next(line for line in detail if line["item_sku"] == "PUMP-1")

    assert Decimal(pump_line["safety_stock"]) == Decimal("5")
    assert Decimal(pump_line["in_production"]) == Decimal("10")
    reconciled = (
        Decimal(pump_line["gross_demand"])
        - Decimal(pump_line["on_hand"])
        - Decimal(pump_line["on_order"])
        - Decimal(pump_line["on_draft"])
        - Decimal(pump_line["in_production"])
        + Decimal(pump_line["safety_stock"])
    )
    assert reconciled == Decimal(pump_line["net_requirement"])


async def test_detail_is_empty_when_nothing_is_planned(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["detail"] == []


async def test_suggested_qty_is_absent_without_consumption_history(client):
    # An active BOM guarantees PUMP-A2 a row (Fix 1's consumed-only filter does
    # not apply to it), with no plan and no consumption history to suggest from.
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 1)])

    grid = await _grid(client, token)

    assert _product(grid, "PUMP-A2")["suggested_qty"] is None


async def test_suggested_qty_is_offered_only_while_nothing_is_planned(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    widget = await _create_item(client, token, "WIDGET-1")
    await client.post(
        f"/inventory/{widget['id']}/adjust",
        json={"quantity": "1000", "reason_code": "found_stock"},
        headers=headers,
    )
    for _ in range(10):
        consume = await client.post(
            f"/inventory/{widget['id']}/consume", json={"quantity": "7"}, headers=headers
        )
        assert consume.status_code == 200, consume.text

    grid = await _grid(client, token)
    # WIDGET-1 has no BOM and no plan - a suggestion is its only claim to a
    # row, so the row must be present (Fix 1's consumed-only filter keeps it).
    assert "WIDGET-1" in {row["item_sku"] for row in grid["products"]}
    suggested = _product(grid, "WIDGET-1")["suggested_qty"]
    assert suggested is not None and Decimal(suggested) > 0

    await _plan(client, token, widget, 5)
    grid_after = await _grid(client, token)
    assert _product(grid_after, "WIDGET-1")["suggested_qty"] is None


async def test_consumed_only_item_without_a_suggestion_gets_no_row(client, test_engine):
    """SEAL-V2 has no BOM and no forecast - consumption history is its only
    claim to a row. Its one consumption event is backdated past the
    moving-average forecast's 7-day window (but still inside the 90-day
    lookback that decides whether the item counts as "consumed" at all), so
    compute_weekly_suggestions offers nothing. Fix 1 must drop the row rather
    than show it in_stock/planned_qty 0/detail []: an empty row nobody asked
    for."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-V2")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "100", "reason_code": "found_stock"},
        headers=headers,
    )
    consume = await client.post(
        f"/inventory/{seal['id']}/consume", json={"quantity": "1"}, headers=headers
    )
    assert consume.status_code == 200, consume.text

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.execute(
            update(InventoryTransaction)
            .where(InventoryTransaction.item_id == uuid.UUID(seal["id"]))
            .where(InventoryTransaction.transaction_type == "consumption")
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=30))
        )
        await session.commit()

    grid = await _grid(client, token)

    assert "SEAL-V2" not in {row["item_sku"] for row in grid["products"]}
