from __future__ import annotations


import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.work_order import WorkOrderComponent, WorkOrderComponentLot
from tests._work_orders_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
    _in_days,
    _give_stock,
    _receive_into_lot,
    _start,
)

pytestmark = pytest.mark.asyncio


async def test_starting_consumes_the_stored_requirements(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    res = await _start(client, token, job)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "in_progress"
    assert res.json()["started_at"] is not None

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("80")


async def test_a_shortfall_on_one_component_rolls_back_the_others(client):
    """One transaction, so a job short on its third component must undo the
    first two. A single-component job cannot prove this."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    plenty_a = await _create_item(client, token, "PLENTY-A")
    plenty_b = await _create_item(client, token, "PLENTY-B")
    short = await _create_item(client, token, "SHORT-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(plenty_a, 1), (plenty_b, 1), (short, 1)])
    await _give_stock(client, token, plenty_a, 100)
    await _give_stock(client, token, plenty_b, 100)
    await _give_stock(client, token, short, 1)
    job = await _raise_job(client, token, pump, qty=10)

    res = await _start(client, token, job)
    assert res.status_code == 409

    for item, expected in ((plenty_a, "100"), (plenty_b, "100"), (short, "1")):
        inv = await client.get(f"/inventory/{item['id']}", headers=headers)
        assert Decimal(inv.json()["quantity_on_hand"]) == Decimal(expected), item["sku"]
    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["status"] == "scheduled"


async def test_the_more_urgent_job_wins_contested_stock(client):
    """Claims rank by scheduled_start, so a job due sooner starts even though
    a later job was raised first and holds the material."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    later = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(30))
    sooner = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, sooner)).status_code == 200

    refused = await _start(client, token, later)
    assert refused.status_code == 409
    assert "SEAL-1" in refused.json()["detail"]


async def test_a_later_job_cannot_block_a_sooner_one(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(30))
    sooner = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, sooner)).status_code == 200, (
        "a job due next month must not hold stock a job due this week needs"
    )


async def test_a_scheduled_sooner_job_holds_stock_against_a_later_one(client):
    """The reservation itself, with nothing else able to explain the refusal:
    150 on the shelf covers either job alone, so the later one is only short
    because the sooner one is holding 100 it has not consumed yet."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    # Left scheduled, never started, so no stock has actually moved.
    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))
    later = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(30))

    refused = await _start(client, token, later)
    assert refused.status_code == 409, refused.text
    assert "SEAL-1: 50 short at" in refused.json()["detail"]
    assert "WO-1 needs them on" in refused.json()["detail"]


async def test_a_job_scheduled_beyond_the_horizon_holds_nothing(client):
    """The holder is due sooner, so only the horizon bound keeps it from
    reserving. A job past the horizon is intent, not a claim on the shelf."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(100))
    far = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(200))

    assert (await _start(client, token, far)).status_code == 200


async def test_a_job_does_not_block_itself(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 10)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200


async def test_starting_twice_is_refused(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200
    assert (await _start(client, token, job)).status_code == 409

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("90"), "stock must come off once"


async def test_a_started_job_cannot_be_edited(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=headers
    )
    assert res.status_code == 409


async def test_starting_a_lot_tracked_component_draws_from_lots(client, test_engine):
    """A lot-tracked component cannot go through apply_inventory_delta, so
    this pins the other branch."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "TRACKED-SEAL", is_lot_tracked=True)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _receive_into_lot(client, token, seal, qty=100, lot_number="LOT-A")
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _start(client, token, job)).status_code == 200

    lots = await client.get("/lots", headers=headers)
    remaining = sum(Decimal(lot["quantity_remaining"]) for lot in lots.json())
    assert remaining == Decimal("80")

    # The lot split is written nowhere else, so if this row were dropped a
    # later cancel or under-run return could not put stock back where it came
    # from. No API exposes it yet, hence the direct read.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(WorkOrderComponentLot.lot_id, WorkOrderComponentLot.quantity).join(
                    WorkOrderComponent,
                    WorkOrderComponent.id == WorkOrderComponentLot.wo_component_id,
                )
                .where(WorkOrderComponent.wo_id == uuid.UUID(job["id"]))
            )
        ).all()
    assert [(str(lot_id), Decimal(qty)) for lot_id, qty in rows] == [
        (lots.json()[0]["id"], Decimal("20"))
    ]
