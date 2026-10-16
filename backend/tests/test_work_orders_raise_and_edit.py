from __future__ import annotations


from decimal import Decimal

import pytest

from tests._work_orders_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
)

pytestmark = pytest.mark.asyncio


async def test_production_feature_is_enabled_for_a_new_test_org(client):
    """Guards the conftest grant: every route in this plan 403s without it."""
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "production" in res.json()["enabled_features"]


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
