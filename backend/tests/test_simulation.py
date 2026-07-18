from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

BASELINE = {
    "shifts_per_day": 2, "shift_hours": 8, "workers_per_shift": 25, "avg_wage_per_hour": 20,
    "machine_count": 10, "throughput_per_shift": 1000, "oee_pct": 75.0,
    "demand_units_per_day": 1800, "defect_rate_pct": 3.5,
    "scrap_cost_per_unit": 5.0, "raw_material_cost_per_unit": 15.0, "selling_price_per_unit": 40.0,
}

OEE_CSV = (
    b"date,shift,machine,planned_time,operating_time,total_units,good_units\n"
    b"2026-01-01,A,M1,480,450,900,870\n"
    b"2026-01-01,B,M1,480,460,920,890\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=OEE_CSV):
    res = await client.post("/datasets", files={"file": ("simulation.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _create_simulation(client, token, **overrides):
    payload = {"name": "Base plant", "description": "", "baseline": BASELINE, "changes": {}}
    payload.update(overrides)
    res = await client.post("/simulation", headers=_h(token), json=payload)
    assert res.status_code == 201, res.text
    return res.json()


async def test_simulation_baseline_derivation(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post("/simulation/baseline", headers=_h(token), json={"dataset_id": dataset_id})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["dataset_id"] == dataset_id
    assert body["baseline"]["shifts_per_day"] == 2.0
    assert body["baseline"]["shift_hours"] == 8.0
    assert body["baseline"]["machine_count"] == 1.0
    assert body["baseline"]["throughput_per_shift"] == 910.0
    assert body["baseline"]["oee_pct"] == 91.67
    assert body["baseline"]["defect_rate_pct"] == 3.3
    assert body["baseline"]["demand_units_per_day"] == 1760.0
    assert body["baseline"]["avg_wage_per_hour"] == 20.0
    assert body["provenance"]["rows"] == 2
    assert body["provenance"]["performance_estimated"] is True
    assert "avg_wage_per_hour" in body["assumptions"]


async def test_simulation_run_adhoc_is_not_persisted(client):
    token = await _register_and_login(client)

    res = await client.post(
        "/simulation/run", headers=_h(token),
        json={"scenario_name": "Add a shift", "baseline": BASELINE, "changes": {"shifts_per_day": 3}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["baseline"]["daily_capacity"] == 2000.0
    assert body["projected"]["daily_capacity"] == 3000.0
    assert body["delta"]["daily_capacity"] == {"absolute": 1000.0, "pct": 50.0, "direction": "increase"}
    assert body["baseline"]["daily_margin"] == 33650.0
    assert body["projected"]["daily_margin"] == 14475.0

    listed = await client.get("/simulation", headers=_h(token))
    assert listed.json() == []


async def test_simulation_crud(client):
    token = await _register_and_login(client)
    sim = await _create_simulation(client, token, name="Base plant")
    assert sim["name"] == "Base plant"
    assert sim["dataset_id"] is None

    listed = await client.get("/simulation", headers=_h(token))
    assert len(listed.json()) == 1

    updated = await client.put(f"/simulation/{sim['id']}", headers=_h(token), json={"description": "updated"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"

    deleted = await client.delete(f"/simulation/{sim['id']}", headers=_h(token))
    assert deleted.status_code == 204

    listed_after = await client.get("/simulation", headers=_h(token))
    assert listed_after.json() == []


async def test_simulation_run_persisted_history_and_compare(client):
    token = await _register_and_login(client)
    sim = await _create_simulation(client, token)

    run_a = await client.post(f"/simulation/{sim['id']}/run", headers=_h(token), json={"changes": {"shifts_per_day": 3}})
    assert run_a.status_code == 201, run_a.text
    assert run_a.json()["result"]["projected"]["daily_capacity"] == 3000.0

    run_b = await client.post(f"/simulation/{sim['id']}/run", headers=_h(token), json={"changes": {}})
    assert run_b.status_code == 201, run_b.text
    assert run_b.json()["result"]["projected"]["daily_capacity"] == 2000.0

    history = await client.get(f"/simulation/{sim['id']}/runs", headers=_h(token))
    assert history.status_code == 200
    assert len(history.json()) == 2

    compare = await client.post(
        "/simulation/compare", headers=_h(token),
        json={"run_a_id": run_a.json()["id"], "run_b_id": run_b.json()["id"]},
    )
    assert compare.status_code == 200, compare.text
    delta = compare.json()["delta"]
    # compare_snapshots(a, b) reads as "how does a (run_a) differ from b (run_b) as baseline":
    # run_a's capacity (3000) is 1000 higher than run_b's (2000) -> increase.
    assert delta["daily_capacity"] == {"absolute": 1000.0, "pct": 50.0, "direction": "increase"}


async def test_simulation_run_export(client):
    token = await _register_and_login(client)
    sim = await _create_simulation(client, token)
    run = await client.post(f"/simulation/{sim['id']}/run", headers=_h(token), json={"changes": {"shifts_per_day": 3}})
    run_id = run.json()["id"]

    csv_res = await client.get(f"/simulation/{sim['id']}/runs/{run_id}/export", headers=_h(token))
    assert csv_res.status_code == 200, csv_res.text
    assert csv_res.headers["content-type"].startswith("text/csv")
    assert b"daily_capacity" in csv_res.content

    xlsx_res = await client.get(
        f"/simulation/{sim['id']}/runs/{run_id}/export", params={"fmt": "xlsx"}, headers=_h(token)
    )
    assert xlsx_res.status_code == 200, xlsx_res.text
    assert xlsx_res.content.startswith(b"PK\x03\x04")

    bad_fmt = await client.get(
        f"/simulation/{sim['id']}/runs/{run_id}/export", params={"fmt": "pdf"}, headers=_h(token)
    )
    assert bad_fmt.status_code == 400

    missing_run = "00000000-0000-0000-0000-000000000000"
    not_found = await client.get(f"/simulation/{sim['id']}/runs/{missing_run}/export", headers=_h(token))
    assert not_found.status_code == 404


async def test_simulation_not_found(client):
    token = await _register_and_login(client)
    res = await client.get("/simulation/00000000-0000-0000-0000-000000000000", headers=_h(token))
    assert res.status_code == 404


async def test_simulation_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    sim = await _create_simulation(client, token_a)

    res_b = await client.get(f"/simulation/{sim['id']}", headers=_h(token_b))
    assert res_b.status_code == 404


async def test_simulation_run_export_tenant_isolation(client):
    token_a = await _register_and_login(client, email="runa@example.com", org_name="RunOrgA")
    sim = await _create_simulation(client, token_a)
    run = await client.post(f"/simulation/{sim['id']}/run", headers=_h(token_a), json={"changes": {}})
    run_id = run.json()["id"]

    token_b = await _register_and_login(client, email="runb@example.com", org_name="RunOrgB")
    res = await client.get(f"/simulation/{sim['id']}/runs/{run_id}/export", headers=_h(token_b))
    assert res.status_code == 404
    res_list = await client.get(f"/simulation/{sim['id']}/runs", headers=_h(token_b))
    assert res_list.status_code == 404
