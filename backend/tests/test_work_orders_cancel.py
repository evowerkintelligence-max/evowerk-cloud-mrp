from __future__ import annotations


from decimal import Decimal

import pytest

from tests._work_orders_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
    _in_days,
    _give_stock,
    _receive_into_lot,
    _start,
    _finish,
    _running_job,
    _cancel,
)

pytestmark = pytest.mark.asyncio


async def test_cancelling_a_scheduled_job_moves_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)

    res = await _cancel(client, token, job)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "cancelled"

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("100")


async def test_cancelling_a_running_job_puts_everything_back(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10, stock=100)

    res = await _cancel(client, token, job)
    assert res.status_code == 200, res.text

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("100"), "stock as it was before start"


async def test_cancelling_a_running_job_makes_nothing(client):
    """It is defined in terms of the finish path, so the guard that it does not
    also stock the product has to be pinned."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10, stock=100)

    assert (await _cancel(client, token, job)).status_code == 200

    inv = await client.get(f"/inventory/{pump['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal(0)
    assert Decimal((await client.get(f"/work-orders/{job['id']}", headers=headers)).json()[
        "quantity_good"
    ]) == Decimal(0)


async def test_cancelling_restores_the_exact_lot_split(client):
    """Not merely the right total: the stock has to go back into the lots it
    came out of, or FEFO ordering and expiry tracking quietly rot."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "TRACKED-SEAL", is_lot_tracked=True)
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _receive_into_lot(client, token, seal, qty=60, lot_number="LOT-A")
    await _receive_into_lot(client, token, seal, qty=40, lot_number="LOT-B")

    job = await _raise_job(client, token, pump, qty=80)
    assert (await _start(client, token, job)).status_code == 200

    assert (await _cancel(client, token, job)).status_code == 200

    lots = {
        lot["lot_number"]: Decimal(lot["quantity_remaining"])
        for lot in (await client.get("/lots", headers=headers)).json()
    }
    assert lots["LOT-A"] == Decimal("60"), lots
    assert lots["LOT-B"] == Decimal("40"), lots


async def test_cancelling_releases_the_stock_for_another_job(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    holder = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))
    later = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(20))
    assert (await _start(client, token, later)).status_code == 409

    assert (await _cancel(client, token, holder)).status_code == 200
    assert (await _start(client, token, later)).status_code == 200


async def test_a_finished_job_cannot_be_cancelled(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10)
    await _finish(client, token, job, good=10)

    assert (await _cancel(client, token, job)).status_code == 409


async def test_cancelling_twice_is_refused(client):
    """The second cancel of a running job would otherwise return its
    components a second time and invent stock."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _running_job(client, token, seal, pump, planned=10, stock=100)

    assert (await _cancel(client, token, job)).status_code == 200
    assert (await _cancel(client, token, job)).status_code == 409

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal("100"), "stock comes back once"
