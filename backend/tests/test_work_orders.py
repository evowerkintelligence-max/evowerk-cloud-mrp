from __future__ import annotations


from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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


async def test_a_job_scheduled_beyond_the_horizon_holds_nothing(client):
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 150)

    await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(400))
    near = await _raise_job(client, token, pump, qty=100, scheduled_start=_in_days(2))

    assert (await _start(client, token, near)).status_code == 200


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


async def test_starting_a_lot_tracked_component_draws_from_lots(client):
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
