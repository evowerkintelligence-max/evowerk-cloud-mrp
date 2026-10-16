from __future__ import annotations


from decimal import Decimal

import pytest

from tests._work_orders_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
    _give_stock,
    _receive_into_lot,
    _start,
    _finish,
    _running_job,
)

pytestmark = pytest.mark.asyncio


async def test_finishing_stocks_only_the_good_units(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=47, scrap=3, scrap_reason="porosity")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert body["quantity_good"] == "47.0000"
    assert body["quantity_scrapped"] == "3.0000"
    assert body["scrap_reason"] == "porosity"
    assert body["finished_at"] is not None

    inv = await client.get(f"/inventory/{pump['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("47"), "scrap must not be stocked"


async def test_an_under_run_requires_saying_what_happened_to_the_remainder(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=30, scrap=2)
    assert res.status_code == 400


async def test_an_under_run_can_return_the_unused_components(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50, stock=200)
    # 50 seals consumed at start, 150 left.

    res = await _finish(
        client, token, job, good=30, scrap=2, remainder_disposition="not_started"
    )
    assert res.status_code == 200, res.text
    assert res.json()["remainder_disposition"] == "not_started"

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("168"), (
        "18 unmade units worth of seals go back on the shelf"
    )


async def test_an_under_run_can_write_the_remainder_off(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50, stock=200)

    res = await _finish(client, token, job, good=30, scrap=2, remainder_disposition="consumed")
    assert res.status_code == 200, res.text

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("150"), "nothing comes back"


async def test_meeting_the_planned_quantity_needs_no_disposition(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    assert (await _finish(client, token, job, good=50)).status_code == 200


async def test_a_disposition_without_an_under_run_is_refused(client):
    """The stored value has to mean something: recording not_started on a job
    that made everything would claim components came back that never did."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=50, remainder_disposition="not_started")
    assert res.status_code == 400


async def test_the_over_run_guard_allows_a_small_batch_overshoot(client):
    """max(2 x planned, planned + 10): a plain doubling would refuse a job
    planned for 1 that yielded 3, which is routine on a prototype line."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=1)

    assert (await _finish(client, token, job, good=3)).status_code == 200


async def test_the_over_run_guard_refuses_a_typo(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    res = await _finish(client, token, job, good=5000)
    assert res.status_code == 400
    assert "50" in res.json()["detail"]


async def test_finishing_rejects_a_negative_quantity(client):
    """Pinned separately from the under-run and over-run guards: both of these
    bodies clear those, so only the sign check can refuse them."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=50)

    negative_good = await _finish(
        client, token, job, good=-1, remainder_disposition="not_started"
    )
    assert negative_good.status_code == 400
    assert (await _finish(client, token, job, good=51, scrap=-1)).status_code == 400


async def test_finishing_a_lot_tracked_product_needs_a_lot_number(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "TRACKED-PUMP", is_lot_tracked=True)
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10)

    assert (await _finish(client, token, job, good=10)).status_code == 400
    ok = await _finish(client, token, job, good=10, lot_number="WO-LOT-1")
    assert ok.status_code == 200, ok.text


async def test_finishing_into_an_existing_lot_number_adds_to_that_lot(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "TRACKED-PUMP", is_lot_tracked=True)
    await _create_active_bom(client, token, pump, [(seal, 1)])

    first = await _running_job(client, token, seal, pump, planned=10, stock=100)
    await _finish(client, token, first, good=10, lot_number="BATCH-9")
    second = await _raise_job(client, token, pump, qty=5)
    await _start(client, token, second)
    await _finish(client, token, second, good=5, lot_number="BATCH-9")

    lots = await client.get("/lots", headers=headers)
    batches = [lot for lot in lots.json() if lot["lot_number"] == "BATCH-9"]
    assert len(batches) == 1, "a repeat lot number is the same physical batch"
    assert Decimal(batches[0]["quantity_remaining"]) == Decimal("15")


async def test_an_under_run_returns_lot_stock_to_the_lots_it_came_from(client):
    """The proportional return has a second path for lot-tracked components,
    and putting the stock back in the wrong lots quietly rots FEFO."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "TRACKED-SEAL", is_lot_tracked=True)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _receive_into_lot(client, token, seal, qty=60, lot_number="LOT-A")
    await _receive_into_lot(client, token, seal, qty=40, lot_number="LOT-B")

    job = await _raise_job(client, token, pump, qty=80)
    assert (await _start(client, token, job)).status_code == 200
    # FEFO drew 60 from LOT-A and 20 from LOT-B.

    res = await _finish(client, token, job, good=40, remainder_disposition="not_started")
    assert res.status_code == 200, res.text

    lots = {
        lot["lot_number"]: Decimal(lot["quantity_remaining"])
        for lot in (await client.get("/lots", headers=headers)).json()
    }
    # Half the 80 comes back, largest draw first, so all 40 land in LOT-A.
    assert lots["LOT-A"] == Decimal("40"), lots
    assert lots["LOT-B"] == Decimal("20"), lots


async def test_finishing_a_job_that_is_not_running_is_refused(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    assert (await _finish(client, token, job, good=10)).status_code == 409
