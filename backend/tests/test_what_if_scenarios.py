from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

CSV = b"machine,units\nM1,10\nM1,20\nM2,5\nM2,7\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=CSV):
    res = await client.post("/datasets", files={"file": ("scenario.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _base_query():
    return {"select": [{"column": "units", "agg": "sum", "alias": "total_units"}], "filters": [], "limit": 500}


async def test_scenario_crud(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/scenarios", headers=_h(token),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    assert create.status_code == 201, create.text
    scenario = create.json()
    assert scenario["name"] == "Base case"

    listed = await client.get("/scenarios", headers=_h(token))
    assert len(listed.json()) == 1

    updated = await client.put(f"/scenarios/{scenario['id']}", headers=_h(token), json={"description": "updated"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"

    deleted = await client.delete(f"/scenarios/{scenario['id']}", headers=_h(token))
    assert deleted.status_code == 204

    listed_after = await client.get("/scenarios", headers=_h(token))
    assert listed_after.json() == []


async def test_scenario_execute_with_param_override(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/scenarios", headers=_h(token),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    scenario_id = create.json()["id"]

    # No override: sum over all rows (10+20+5+7=42).
    run_all = await client.post(f"/scenarios/{scenario_id}/execute", headers=_h(token), json={"param_sets": [{}]})
    assert run_all.status_code == 200, run_all.text
    assert run_all.json()[0]["result"]["rows"][0]["total_units"] == 42

    # Override adds a filter scoping to M1 only (10+20=30).
    override = {"filters": [{"column": "machine", "op": "eq", "value": "M1"}]}
    run_m1 = await client.post(f"/scenarios/{scenario_id}/execute", headers=_h(token), json={"param_sets": [override]})
    assert run_m1.status_code == 200, run_m1.text
    assert run_m1.json()[0]["result"]["rows"][0]["total_units"] == 30


async def test_list_and_export_scenario_runs(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/scenarios", headers=_h(token),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    scenario_id = create.json()["id"]

    exec_res = await client.post(f"/scenarios/{scenario_id}/execute", headers=_h(token), json={"param_sets": [{}]})
    assert exec_res.status_code == 200, exec_res.text

    listed = await client.get(f"/scenarios/{scenario_id}/runs", headers=_h(token))
    assert listed.status_code == 200, listed.text
    runs = listed.json()
    assert len(runs) == 1
    run_id = runs[0]["id"]
    assert runs[0]["result"]["rows"][0]["total_units"] == 42

    csv_res = await client.get(f"/scenarios/{scenario_id}/runs/{run_id}/export", headers=_h(token))
    assert csv_res.status_code == 200, csv_res.text
    assert csv_res.headers["content-type"].startswith("text/csv")
    assert b"total_units" in csv_res.content
    assert b"42" in csv_res.content

    xlsx_res = await client.get(
        f"/scenarios/{scenario_id}/runs/{run_id}/export", params={"fmt": "xlsx"}, headers=_h(token)
    )
    assert xlsx_res.status_code == 200, xlsx_res.text
    assert xlsx_res.content.startswith(b"PK\x03\x04")

    bad_fmt = await client.get(
        f"/scenarios/{scenario_id}/runs/{run_id}/export", params={"fmt": "pdf"}, headers=_h(token)
    )
    assert bad_fmt.status_code == 400

    missing_run = "00000000-0000-0000-0000-000000000000"
    not_found = await client.get(f"/scenarios/{scenario_id}/runs/{missing_run}/export", headers=_h(token))
    assert not_found.status_code == 404


async def test_scenario_run_export_tenant_isolation(client):
    token_a = await _register_and_login(client, email="runa@example.com", org_name="RunOrgA")
    dataset_id = await _upload(client, token_a)
    create = await client.post(
        "/scenarios", headers=_h(token_a),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    scenario_id = create.json()["id"]
    exec_res = await client.post(f"/scenarios/{scenario_id}/execute", headers=_h(token_a), json={"param_sets": [{}]})
    run_id = (await client.get(f"/scenarios/{scenario_id}/runs", headers=_h(token_a))).json()[0]["id"]
    assert exec_res.status_code == 200

    token_b = await _register_and_login(client, email="runb@example.com", org_name="RunOrgB")
    res = await client.get(f"/scenarios/{scenario_id}/runs/{run_id}/export", headers=_h(token_b))
    assert res.status_code == 404
    res_list = await client.get(f"/scenarios/{scenario_id}/runs", headers=_h(token_b))
    assert res_list.status_code == 404


async def test_scenario_compare(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/scenarios", headers=_h(token),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    scenario_id = create.json()["id"]

    res = await client.post(
        "/scenarios/compare", headers=_h(token),
        json={
            "scenario_a_id": scenario_id, "params_a": {"filters": [{"column": "machine", "op": "eq", "value": "M1"}]},
            "scenario_b_id": scenario_id, "params_b": {"filters": [{"column": "machine", "op": "eq", "value": "M2"}]},
            "metric": "total_units",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["value_a"] == 30
    assert body["value_b"] == 12
    assert body["delta"] == pytest.approx(-18.0)


async def test_scenario_not_found(client):
    token = await _register_and_login(client)
    res = await client.get("/scenarios/00000000-0000-0000-0000-000000000000", headers=_h(token))
    assert res.status_code == 404


async def test_scenario_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)

    create = await client.post(
        "/scenarios", headers=_h(token_a),
        json={"name": "Base case", "dataset_id": dataset_id, "base_query": _base_query()},
    )
    scenario_id = create.json()["id"]

    res_b = await client.get(f"/scenarios/{scenario_id}", headers=_h(token_b))
    assert res_b.status_code == 404
