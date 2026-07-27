from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="pacewo@example.com", org_name="PaceOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


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


async def _raise_job(client, token, item, qty=10):
    res = await client.post(
        "/work-orders",
        json={"item_id": item["id"], "quantity_planned": str(qty)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


async def _start_session(client, headers, work_order_id=None):
    body = {"line": "Line 1", "operator": "J. Alvarez", "target_rate": None}
    if work_order_id is not None:
        body["work_order_id"] = work_order_id
    return await client.post("/pace/sessions", headers=headers, json=body)


async def _run_and_stop(client, headers, session_id, good=5):
    for _ in range(good):
        tap = await client.post(
            f"/pace/sessions/{session_id}/tap", headers=headers, json={"kind": "good"}
        )
        assert tap.status_code == 200
    stopped = await client.post(f"/pace/sessions/{session_id}/stop", headers=headers)
    assert stopped.status_code == 200, stopped.text
    return stopped.json()


async def _dataset(client, headers, dataset_id):
    res = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _column_names(dataset):
    return [column["name"] for column in dataset["columns"]]


async def _running_job(client, token):
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    assert (await _start(client, token, job)).status_code == 200
    return job, pump


async def test_a_session_can_be_started_from_a_job(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    job, _pump = await _running_job(client, token)

    res = await _start_session(client, headers, work_order_id=job["id"])
    assert res.status_code == 201, res.text
    assert res.json()["work_order_id"] == job["id"]
    assert res.json()["wo_number"] == job["wo_number"]
    assert res.json()["product_sku"] == "PUMP-1"


async def test_a_linked_sessions_dataset_carries_the_product(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    job, pump = await _running_job(client, token)

    session = await _start_session(client, headers, work_order_id=job["id"])
    stopped = await _run_and_stop(client, headers, session.json()["id"])

    dataset = await _dataset(client, headers, stopped["dataset_id"])
    assert "product" in _column_names(dataset), _column_names(dataset)
    assert dataset["preview"][0]["product"] == pump["sku"]


async def test_an_unlinked_sessions_dataset_is_unchanged(client):
    """The backward-compatibility guard: every shipped OEE dashboard reads
    these datasets, so a session with no job must produce exactly the columns
    it produces today."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    session = await _start_session(client, headers)
    assert session.status_code == 201
    assert session.json()["work_order_id"] is None
    stopped = await _run_and_stop(client, headers, session.json()["id"])

    dataset = await _dataset(client, headers, stopped["dataset_id"])
    assert _column_names(dataset) == [
        "line",
        "operator",
        "planned_min",
        "operating_min",
        "total_units",
        "good_units",
        "unplanned_dt_min",
        "planned_dt_min",
    ]


async def test_a_session_cannot_link_to_another_orgs_job(client):
    token_a = await _register_and_login(client)
    job, _pump = await _running_job(client, token_a)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await _start_session(
        client, {"Authorization": f"Bearer {token_b}"}, work_order_id=job["id"]
    )
    assert res.status_code in (400, 404), res.text


async def test_a_session_cannot_link_to_a_job_that_does_not_exist(client):
    token = await _register_and_login(client)
    res = await _start_session(
        client,
        {"Authorization": f"Bearer {token}"},
        work_order_id="00000000-0000-0000-0000-000000000000",
    )
    assert res.status_code in (400, 404), res.text
