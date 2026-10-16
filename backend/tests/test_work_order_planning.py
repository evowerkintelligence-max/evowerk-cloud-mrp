from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


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


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per)."""
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


async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


async def _finish(client, token, job, good, scrap="0", **extra):
    body = {"quantity_good": str(good), "quantity_scrapped": str(scrap)}
    body.update(extra)
    return await client.post(
        f"/work-orders/{job['id']}/finish",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _plan_lines(client, token):
    res = await client.get("/mrp/plan", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return {line["item_sku"]: line for line in res.json()["lines"]}


async def _plan_for(client, token, item, qty, when=None):
    res = await client.put(
        f"/planning/grid/{item['id']}",
        json={"quantity": str(qty), "needed_by": when or _in_days(30)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text


async def test_a_running_job_is_incoming_supply(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan_for(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)

    before = await _plan_lines(client, token)
    assert Decimal(before["PUMP-1"]["net_requirement"]) == Decimal("50")

    job = await _raise_job(client, token, pump, qty=50)
    await _start(client, token, job)

    after = await _plan_lines(client, token)
    assert Decimal(after["PUMP-1"]["net_requirement"]) == Decimal(0), (
        "a job in flight must not be planned a second time"
    )
    assert Decimal(after["PUMP-1"]["in_production"]) == Decimal("50")


async def test_a_scheduled_job_still_plans_its_components(client):
    """The trap: supply without component demand would leave the grid saying
    all in stock for a job that cannot actually run."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(5))

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "a scheduled job must still ask for its own components"
    )


async def test_a_running_job_does_not_re_raise_component_demand(client):
    """Its components are already consumed and on_hand reflects that, so
    counting them again would buy them twice."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _give_stock(client, token, seal, 20)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    lines = await _plan_lines(client, token)
    assert "SEAL-1" not in lines or Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal(0)


async def test_a_scheduled_job_is_arithmetically_neutral(client):
    """Forecast 50 with a scheduled job for 50 must ask for exactly the same
    components as the forecast alone would."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _plan_for(client, token, pump, 50)

    without = await _plan_lines(client, token)
    baseline = Decimal(without["SEAL-1"]["net_requirement"])

    await _raise_job(client, token, pump, qty=50, scheduled_start=_in_days(5))
    with_job = await _plan_lines(client, token)

    assert Decimal(with_job["SEAL-1"]["net_requirement"]) == baseline == Decimal("100")


async def test_a_job_beyond_the_horizon_is_not_in_the_plan(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(400))

    assert await _plan_lines(client, token) == {}, (
        "neither its supply nor its component demand belongs in this window"
    )


async def test_a_job_late_to_start_still_counts(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-7))

    lines = await _plan_lines(client, token)
    assert Decimal(lines["SEAL-1"]["net_requirement"]) == Decimal("20"), (
        "a job that slipped is still going to happen"
    )


async def test_finished_and_cancelled_jobs_count_for_nothing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _plan_for(client, token, pump, 50)
    await _give_stock(client, token, seal, 100)

    done_job = await _raise_job(client, token, pump, qty=20)
    await _start(client, token, done_job)
    await _finish(client, token, done_job, good=20)
    cancelled = await _raise_job(client, token, pump, qty=20)
    await client.post(f"/work-orders/{cancelled['id']}/cancel", headers=headers)

    lines = await _plan_lines(client, token)
    assert Decimal(lines["PUMP-1"]["in_production"]) == Decimal(0)


async def test_a_cancelled_job_stops_asking_for_its_components(client):
    """The demand half of the same rule: a called-off job must not keep the
    plan ordering parts for it."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(5))
    assert Decimal((await _plan_lines(client, token))["SEAL-1"]["net_requirement"]) == Decimal("20")

    await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)

    assert await _plan_lines(client, token) == {}


async def test_the_build_calculator_nets_against_jobs_in_flight(client):
    """compute_build_plan shares the engine, so an ad hoc build must see the
    same supply planning does rather than suggesting a duplicate build."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)

    res = await client.post(
        "/mrp/build-plan",
        json={"item_id": pump["id"], "build_quantity": "10"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    lines = {line["item_sku"]: line for line in res.json()["lines"]}
    assert Decimal(lines["PUMP-1"]["in_production"]) == Decimal("10")
    assert Decimal(lines["PUMP-1"]["net_requirement"]) == Decimal(0)
