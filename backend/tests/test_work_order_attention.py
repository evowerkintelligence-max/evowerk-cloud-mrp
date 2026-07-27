from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.work_order import WorkOrder

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


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


async def _brief(client, token):
    res = await client.get(
        "/inventory/morning-brief", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _backdate_start(test_engine, wo_id, days):
    """No API can produce a job that started in the past, and the stall rule is
    entirely about elapsed time, so this reaches into the test database."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        job = await session.get(WorkOrder, uuid_module.UUID(wo_id))
        job.started_at = datetime.now(timezone.utc) - timedelta(days=days)
        await session.commit()


async def _set_measured_duration(test_engine, wo_id, hours):
    """Give a finished job a real measured duration. Both timestamps have to
    move: a job that started and finished inside the test's own runtime measures
    as zero, and a sample of zeroes cannot tell a median from a mean."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        job = await session.get(WorkOrder, uuid_module.UUID(wo_id))
        job.finished_at = datetime.now(timezone.utc)
        job.started_at = job.finished_at - timedelta(hours=hours)
        await session.commit()


async def test_a_job_late_to_start_is_flagged(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-3))

    alerts = (await _brief(client, token))["job_alerts"]
    assert [(a["wo_number"], a["reason"]) for a in alerts] == [(1, "late_to_start")]
    assert alerts[0]["item_sku"] == "PUMP-1"
    assert alerts[0]["days_over"] == 3


async def test_a_job_due_later_is_not_flagged(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(3))

    assert (await _brief(client, token))["job_alerts"] == []


async def test_a_job_with_no_date_is_not_late(client):
    """An undated job outranks every dated one for stock, but it has no date to
    be late against, so flagging it would put a permanent alert on the brief."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _raise_job(client, token, pump, qty=10)

    assert (await _brief(client, token))["job_alerts"] == []


async def test_job_alerts_are_org_scoped(client):
    token_a = await _register_and_login(client)
    seal = await _create_item(client, token_a, "SEAL-1")
    pump = await _create_item(client, token_a, "PUMP-1")
    await _create_active_bom(client, token_a, pump, [(seal, 1)])
    await _raise_job(client, token_a, pump, qty=10, scheduled_start=_in_days(-3))

    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")
    assert (await _brief(client, token_b))["job_alerts"] == []


async def test_a_job_running_far_too_long_is_flagged_stalled(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=60)

    alerts = (await _brief(client, token))["job_alerts"]
    assert [(a["wo_number"], a["reason"]) for a in alerts] == [(1, "stalled")]


async def test_a_job_running_a_normal_length_of_time_is_not_flagged(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=1)

    assert (await _brief(client, token))["job_alerts"] == []


async def test_history_sets_the_bar_when_there_is_no_routing(client, test_engine):
    """Five quick jobs teach the app that this item takes about an hour, so a
    sixth still running after three days is stalled even though the cold-start
    constant would have allowed it seven."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)

    for _ in range(5):
        done = await _raise_job(client, token, pump, qty=10)
        await _start(client, token, done)
        assert (await _finish(client, token, done, good=10)).status_code == 200
    # Those finished within the test's own runtime, so their measured
    # duration is effectively zero and the +1 day floor governs.

    running = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, running)
    await _backdate_start(test_engine, running["id"], days=3)

    alerts = (await _brief(client, token))["job_alerts"]
    assert any(a["wo_number"] == running["wo_number"] and a["reason"] == "stalled" for a in alerts)


async def test_a_routing_sets_the_bar_ahead_of_history(client, test_engine):
    """The shop's own stated cycle time wins over measured jobs, and a job
    inside it is not stalled even after several days."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)

    work_center = await client.post(
        "/work-centers", json={"code": "ASSY", "name": "Assembly"}, headers=headers
    )
    assert work_center.status_code == 201, work_center.text
    routing = await client.post(f"/routings?item_id={pump['id']}", headers=headers)
    assert routing.status_code == 201, routing.text
    steps = await client.post(
        f"/routings/{routing.json()['id']}/steps",
        json=[
            {
                "sequence": 1,
                "work_center_id": work_center.json()["id"],
                "cycle_time_seconds": 86400.0,
            }
        ],
        headers=headers,
    )
    assert steps.status_code == 200, steps.text
    activate = await client.post(f"/routings/{routing.json()['id']}/activate", headers=headers)
    assert activate.status_code == 200, activate.text

    for _ in range(5):
        done = await _raise_job(client, token, pump, qty=1)
        await _start(client, token, done)
        assert (await _finish(client, token, done, good=1)).status_code == 200

    running = await _raise_job(client, token, pump, qty=1)
    await _start(client, token, running)
    await _backdate_start(test_engine, running["id"], days=1)

    assert (await _brief(client, token))["job_alerts"] == [], (
        "history says minutes, but the routing says a day, and the routing wins"
    )


async def test_a_done_job_is_never_flagged(client, test_engine):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    await _start(client, token, job)
    await _backdate_start(test_engine, job["id"], days=90)
    await _finish(client, token, job, good=10)

    assert (await _brief(client, token))["job_alerts"] == []


async def test_a_cancelled_job_is_never_flagged(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start=_in_days(-30))

    assert (await client.post(f"/work-orders/{job['id']}/cancel", headers=headers)).status_code == 200
    assert (await _brief(client, token))["job_alerts"] == []


async def test_one_job_left_open_over_a_holiday_does_not_move_the_bar(client, test_engine):
    """Median, not mean. Four one-hour jobs and one left open for a month:
    the median still says an hour, so a job running three days is stalled.
    Under a mean the bar would be about 12 days and nothing would be flagged,
    which is exactly what the outlier is there to prevent."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)

    for hours in (1, 1, 1, 1, 720):
        done = await _raise_job(client, token, pump, qty=1)
        await _start(client, token, done)
        assert (await _finish(client, token, done, good=1)).status_code == 200
        await _set_measured_duration(test_engine, done["id"], hours=hours)

    running = await _raise_job(client, token, pump, qty=1)
    await _start(client, token, running)
    await _backdate_start(test_engine, running["id"], days=3)

    alerts = (await _brief(client, token))["job_alerts"]
    assert [(a["wo_number"], a["reason"]) for a in alerts] == [(running["wo_number"], "stalled")]


async def test_two_items_are_judged_by_their_own_history(client, test_engine):
    """Per item, not per org: a shop making both a five-minute bracket and a
    three-week assembly has no single sensible number."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    quick = await _create_item(client, token, "QUICK-1")
    slow = await _create_item(client, token, "SLOW-1")
    await _create_active_bom(client, token, quick, [(seal, 1)])
    await _create_active_bom(client, token, slow, [(seal, 1)])
    await _give_stock(client, token, seal, 1000)

    for item, hours in ((quick, 1), (slow, 720)):
        for _ in range(3):
            done = await _raise_job(client, token, item, qty=1)
            await _start(client, token, done)
            assert (await _finish(client, token, done, good=1)).status_code == 200
            await _set_measured_duration(test_engine, done["id"], hours=hours)

    running_quick = await _raise_job(client, token, quick, qty=1)
    await _start(client, token, running_quick)
    await _backdate_start(test_engine, running_quick["id"], days=3)
    running_slow = await _raise_job(client, token, slow, qty=1)
    await _start(client, token, running_slow)
    await _backdate_start(test_engine, running_slow["id"], days=3)

    alerts = (await _brief(client, token))["job_alerts"]
    flagged = {a["item_sku"] for a in alerts}
    assert flagged == {"QUICK-1"}, (
        "three days is late for the one-hour item and early for the month-long one"
    )
