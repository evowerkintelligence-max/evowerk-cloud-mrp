from __future__ import annotations


import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.work_order import WorkOrderComponent, WorkOrderComponentLot

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


async def test_production_feature_is_enabled_for_a_new_test_org(client):
    """Guards the conftest grant: every route in this plan 403s without it."""
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "production" in res.json()["enabled_features"]


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


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_raising_a_job_computes_its_components_and_moves_no_stock(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    job = await _raise_job(client, token, pump, qty=10)
    assert job["status"] == "scheduled"
    assert job["wo_number"] == 1
    assert [(c["item_sku"], c["quantity_required"]) for c in job["components"]] == [
        ("SEAL-1", "20.0000")
    ]

    inv = await client.get(f"/inventory/{seal['id']}", headers=headers)
    assert Decimal(inv.json()["quantity_on_hand"]) == Decimal(0), "raising must move no stock"


async def test_job_numbers_allocate_sequentially_per_org(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])

    numbers = [(await _raise_job(client, token, pump))["wo_number"] for _ in range(3)]
    assert numbers == [1, 2, 3]


async def test_an_item_with_no_active_bom_cannot_be_built(client):
    token = await _register_and_login(client)
    pump = await _create_item(client, token, "PUMP-1")
    res = await client.post(
        "/work-orders",
        json={"item_id": pump["id"], "quantity_planned": "5"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "PUMP-1" in res.json()["detail"]


async def test_job_rejects_a_nonpositive_or_oversized_quantity(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    headers = {"Authorization": f"Bearer {token}"}

    for bad in ("0", "-5", "100000000"):
        res = await client.post(
            "/work-orders",
            json={"item_id": pump["id"], "quantity_planned": bad},
            headers=headers,
        )
        assert res.status_code == 400, bad


async def test_bom_scrap_pct_is_folded_into_the_requirement(client):
    """load_bom_children already folds scrap_pct in, so the job must not
    apply it a second time."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    bom = await client.post("/boms", json={"item_id": pump["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": seal["id"],
                    "quantity": "2",
                    "sequence": 0,
                    "scrap_pct": "10",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)

    job = await _raise_job(client, token, pump, qty=10)
    assert job["components"][0]["quantity_required"] == "22.0000"


async def test_jobs_are_org_scoped(client):
    token_a = await _register_and_login(client)
    seal = await _create_item(client, token_a, "SEAL-1")
    pump = await _create_item(client, token_a, "PUMP-1")
    await _create_active_bom(client, token_a, pump, [(seal, 2)])
    job = await _raise_job(client, token_a, pump)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    headers_b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/work-orders", headers=headers_b)).json() == []
    assert (await client.get(f"/work-orders/{job['id']}", headers=headers_b)).status_code == 404


async def test_editing_a_scheduled_job_recomputes_its_components(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=50)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "100"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["quantity_planned"] == "100.0000"
    assert res.json()["components"][0]["quantity_required"] == "200.0000"


async def test_editing_picks_up_a_bom_changed_since_the_job_was_raised(client):
    """A job always reflects the recipe as of its last edit, so a corrected
    BOM reaches a job nobody has started."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    bolt = await _create_item(client, token, "BOLT-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10)

    # Supersede the recipe: a new active BOM adding a bolt.
    await _create_active_bom(client, token, pump, [(seal, 2), (bolt, 1)])

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "10"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert sorted(c["item_sku"] for c in res.json()["components"]) == ["BOLT-1", "SEAL-1"]


async def test_editing_can_move_the_date_and_notes(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"scheduled_start": "2030-05-01", "notes": "moved up"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] == "2030-05-01"
    assert res.json()["notes"] == "moved up"


async def test_editing_rejects_a_bad_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    job = await _raise_job(client, token, pump, qty=10)

    for bad in ("0", "-1", "100000000"):
        res = await client.patch(
            f"/work-orders/{job['id']}", json={"quantity_planned": bad}, headers=headers
        )
        assert res.status_code == 400, bad
    # The refused edits left the original quantity alone.
    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["quantity_planned"] == "10.0000"


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _receive_into_lot(client, token, item, qty, lot_number):
    """Gives a lot-tracked item real on-hand stock via a received PO lot. Lot
    stock cannot be seeded any other way, so this needs a supplier too."""
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post(
        "/suppliers", json={"name": f"Acme Metals {lot_number}"}, headers=headers
    )
    assert supplier.status_code == 201, supplier.text
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    recv = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}],
        headers=headers,
    )
    assert recv.status_code == 200, recv.text


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


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


async def _finish(client, token, job, good, scrap="0", **extra):
    body = {"quantity_good": str(good), "quantity_scrapped": str(scrap)}
    body.update(extra)
    return await client.post(
        f"/work-orders/{job['id']}/finish",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _running_job(client, token, seal, pump, planned=50, stock=200):
    await _give_stock(client, token, seal, stock)
    job = await _raise_job(client, token, pump, qty=planned)
    assert (await _start(client, token, job)).status_code == 200
    return job


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


async def _cancel(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/cancel", headers={"Authorization": f"Bearer {token}"}
    )


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


async def _member_headers(client, test_engine, token, email="member@example.com"):
    """A read-only member in the same org. No API creates one, so this writes
    the row directly, following test_customers.py."""
    from app.core.security import hash_password
    from app.models.user import User

    org_id = (
        await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    ).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid.UUID(org_id),
                email=email,
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_a_member_can_read_jobs_but_cannot_touch_any_of_them(client, test_engine):
    """Every mutation is gated. Without this the gates could be deleted outright
    and the whole suite would still pass."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    member = await _member_headers(client, test_engine, token)

    assert (await client.get("/work-orders", headers=member)).status_code == 200
    assert (await client.get(f"/work-orders/{job['id']}", headers=member)).status_code == 200

    raised = await client.post(
        "/work-orders",
        json={"item_id": pump["id"], "quantity_planned": "5"},
        headers=member,
    )
    assert raised.status_code == 403
    assert (
        await client.patch(
            f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=member
        )
    ).status_code == 403
    assert (await client.post(f"/work-orders/{job['id']}/start", headers=member)).status_code == 403
    assert (
        await client.post(
            f"/work-orders/{job['id']}/finish",
            json={"quantity_good": "10", "quantity_scrapped": "0"},
            headers=member,
        )
    ).status_code == 403
    assert (await client.post(f"/work-orders/{job['id']}/cancel", headers=member)).status_code == 403

    # None of the refusals moved anything.
    still = await client.get(f"/work-orders/{job['id']}", headers=member)
    assert still.json()["status"] == "scheduled"
    assert still.json()["quantity_planned"] == "10.0000"


async def test_a_manager_can_run_a_job_end_to_end(client, test_engine):
    """The allowed half of the same gate. Narrowing require_role to owner-only
    would pass every other test in this file."""
    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)

    org_id = (
        await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    ).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid.UUID(org_id),
                email="manager@example.com",
                hashed_password=hash_password(PASSWORD),
                role="manager",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post(
        "/auth/login", json={"email": "manager@example.com", "password": PASSWORD}
    )
    manager = {"Authorization": f"Bearer {login.json()['access_token']}"}

    raised = await client.post(
        "/work-orders", json={"item_id": pump["id"], "quantity_planned": "10"}, headers=manager
    )
    assert raised.status_code == 201, raised.text
    job = raised.json()
    assert (
        await client.patch(f"/work-orders/{job['id']}", json={"quantity_planned": "8"}, headers=manager)
    ).status_code == 200
    assert (await client.post(f"/work-orders/{job['id']}/start", headers=manager)).status_code == 200
    finished = await client.post(
        f"/work-orders/{job['id']}/finish",
        json={"quantity_good": "8", "quantity_scrapped": "0"},
        headers=manager,
    )
    assert finished.status_code == 200, finished.text


async def test_clearing_the_start_date_actually_clears_it(client):
    """null means "clear this", not "field omitted". The date drives reservation
    priority, the horizon bound and the Late badge, so silently keeping the old
    one made the job behave as though the edit never happened."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")
    assert job["scheduled_start"] == "2030-06-01"

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"scheduled_start": None}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] is None

    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["scheduled_start"] is None


async def test_an_omitted_field_is_left_alone(client):
    """The other half: a PATCH that does not mention the date must not wipe it,
    or every quantity edit would silently unschedule the job."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] == "2030-06-01"


async def _create_operator(client, token, name="J Kim"):
    res = await client.post(
        "/operators", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_assign_operator_to_scheduled_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": operator["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_assign_operator_to_in_progress_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    await _give_stock(client, token, component, 100)
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(f"/work-orders/{job['id']}/start", headers=headers)
    assert start.status_code == 200
    operator = await _create_operator(client, token)

    # A running job 409s on a quantity/date/notes edit, but assigning who's
    # actually running it is exactly the thing you'd want to do mid-job.
    res = await client.patch(
        f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_editing_quantity_on_in_progress_job_still_409s(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    await _give_stock(client, token, component, 100)
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(f"/work-orders/{job['id']}/start", headers=headers)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "5"}, headers=headers
    )
    assert res.status_code == 409


async def test_clear_assigned_operator(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers)

    res = await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": None}, headers=headers)
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] is None


async def test_assign_unknown_operator_404(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
