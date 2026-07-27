from __future__ import annotations


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
