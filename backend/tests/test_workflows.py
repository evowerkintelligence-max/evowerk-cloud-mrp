from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="stock.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


def _linear_workflow(dataset_id):
    return {
        "name": "West region export",
        "steps": [
            {"id": "load", "name": "Load stock", "type": "load_dataset", "config": {"dataset_id": dataset_id}},
            {
                "id": "filter_west",
                "name": "Filter to west",
                "type": "filter",
                "depends_on": ["load"],
                "config": {"input_step": "load", "filters": [{"column": "region", "op": "eq", "value": "west"}]},
            },
            {
                "id": "export",
                "name": "Export CSV",
                "type": "export_result",
                "depends_on": ["filter_west"],
                "config": {"input_step": "filter_west", "format": "csv"},
            },
        ],
    }


async def test_cyclic_workflow_rejected_on_create(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "name": "Cycle",
        "steps": [
            {"id": "a", "name": "A", "type": "filter", "depends_on": ["b"], "config": {}},
            {"id": "b", "name": "B", "type": "filter", "depends_on": ["a"], "config": {}},
        ],
    }
    res = await client.post("/workflows", headers=headers, json=body)
    assert res.status_code == 400
    assert "cycle" in res.json()["detail"].lower()


async def test_unknown_dependency_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "name": "Bad dep",
        "steps": [{"id": "a", "name": "A", "type": "filter", "depends_on": ["missing"], "config": {}}],
    }
    res = await client.post("/workflows", headers=headers, json=body)
    assert res.status_code == 400


async def test_linear_workflow_runs_to_success(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    created = await client.post("/workflows", headers=headers, json=_linear_workflow(dataset_id))
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    assert ran.status_code == 202
    run_id = ran.json()["id"]
    # httpx's ASGITransport runs the whole ASGI cycle (including FastAPI
    # BackgroundTasks) inside this await, so the run has already finished by
    # the time this response comes back - no polling needed.

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "success"

    event_types = [e["event_type"] for e in body["events"]]
    assert event_types[0] == "run_started"
    assert event_types[-1] == "run_finished"
    finished = [e for e in body["events"] if e["event_type"] == "node_finished"]
    assert {e["step_id"] for e in finished} == {"load", "filter_west", "export"}
    assert all(e["payload"]["status"] == "success" for e in finished)


async def test_aggregate_step_runs_end_to_end(client):
    """Run-history doesn't expose step output values (see
    test_workflow_steps.py for the actual pandas-aggregation assertions) -
    this just checks the step wires up and succeeds inside a real workflow run."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Aggregate by region",
        "steps": [
            {"id": "load", "name": "Load stock", "type": "load_dataset", "config": {"dataset_id": dataset_id}},
            {
                "id": "agg",
                "name": "Sum qty by region",
                "type": "aggregate",
                "depends_on": ["load"],
                "config": {
                    "input_step": "load",
                    "group_by": ["region"],
                    "select": [{"column": "qty", "agg": "sum", "alias": "total_qty"}],
                },
            },
        ],
    }
    created = await client.post("/workflows", headers=headers, json=body)
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    run_id = ran.json()["id"]

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    result = detail.json()
    assert result["status"] == "success"
    finished = {e["step_id"]: e["payload"] for e in result["events"] if e["event_type"] == "node_finished"}
    assert finished["agg"]["status"] == "success"


async def test_fail_fast_halts_downstream_steps(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Fail fast",
        "failure_policy": "fail_fast",
        "steps": [
            # Missing config.dataset_id -> StepError, this step fails.
            {"id": "bad_sql", "name": "Bad SQL", "type": "sql", "config": {"sql": "SELECT 1"}},
            {
                "id": "after",
                "name": "Never runs",
                "type": "load_dataset",
                "depends_on": ["bad_sql"],
                "config": {"dataset_id": dataset_id},
            },
        ],
    }
    created = await client.post("/workflows", headers=headers, json=body)
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    run_id = ran.json()["id"]

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    result = detail.json()
    assert result["status"] == "failed"

    step_ids_touched = {e["step_id"] for e in result["events"] if e["step_id"]}
    assert step_ids_touched == {"bad_sql"}  # "after" never started or was even marked skipped


async def test_skip_dependents_marks_downstream_skipped_not_failed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Skip dependents",
        "failure_policy": "skip_dependents",
        "steps": [
            {"id": "bad_sql", "name": "Bad SQL", "type": "sql", "config": {"sql": "SELECT 1"}},
            {
                "id": "after",
                "name": "Skipped",
                "type": "load_dataset",
                "depends_on": ["bad_sql"],
                "config": {"dataset_id": dataset_id},
            },
            {"id": "independent", "name": "Independent", "type": "load_dataset", "config": {"dataset_id": dataset_id}},
        ],
    }
    created = await client.post("/workflows", headers=headers, json=body)
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    run_id = ran.json()["id"]

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    result = detail.json()
    assert result["status"] == "partial"

    events_by_step = {}
    for e in result["events"]:
        if e["step_id"]:
            events_by_step.setdefault(e["step_id"], []).append(e["event_type"])
    assert "node_skipped" in events_by_step["after"]
    assert "node_finished" in events_by_step["independent"]


async def test_continue_runs_downstream_steps_despite_upstream_failure(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Continue anyway",
        "failure_policy": "continue",
        "steps": [
            # Missing config.dataset_id -> StepError, this step fails.
            {"id": "bad_sql", "name": "Bad SQL", "type": "sql", "config": {"sql": "SELECT 1"}},
            {
                "id": "after",
                "name": "Attempted anyway",
                "type": "export_result",
                "depends_on": ["bad_sql"],
                "config": {"input_step": "bad_sql", "format": "csv"},
            },
            {"id": "independent", "name": "Independent", "type": "load_dataset", "config": {"dataset_id": dataset_id}},
        ],
    }
    created = await client.post("/workflows", headers=headers, json=body)
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    ran = await client.post(f"/workflows/{workflow_id}/run", headers=headers)
    run_id = ran.json()["id"]

    detail = await client.get(f"/workflows/{workflow_id}/runs/{run_id}", headers=headers)
    result = detail.json()
    assert result["status"] == "partial"

    events_by_step: dict[str, list[str]] = {}
    for e in result["events"]:
        if e["step_id"]:
            events_by_step.setdefault(e["step_id"], []).append(e["event_type"])
    # "after" was actually attempted (not pre-emptively skipped) - it failed on
    # its own missing upstream data, proving continue really does try every
    # step regardless, per its own UI description.
    assert events_by_step["after"] == ["node_started", "node_finished"]
    assert "node_finished" in events_by_step["independent"]


async def test_run_history_lists_across_workflows_with_status_filter_and_org_scoping(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    ok = await client.post("/workflows", headers=headers, json=_linear_workflow(dataset_id))
    ok_id = ok.json()["id"]
    await client.post(f"/workflows/{ok_id}/run", headers=headers)

    bad = await client.post(
        "/workflows",
        headers=headers,
        json={
            "name": "Bad SQL",
            "steps": [{"id": "bad_sql", "name": "Bad SQL", "type": "sql", "config": {"sql": "SELECT 1"}}],
        },
    )
    bad_id = bad.json()["id"]
    await client.post(f"/workflows/{bad_id}/run", headers=headers)

    all_runs = await client.get("/workflows/runs", headers=headers)
    assert all_runs.status_code == 200
    body = all_runs.json()
    assert len(body) == 2
    names = {r["workflow_name"] for r in body}
    assert names == {"West region export", "Bad SQL"}
    # newest first
    assert body[0]["created_at"] >= body[1]["created_at"]

    failed_only = await client.get("/workflows/runs?status=failed", headers=headers)
    assert [r["workflow_name"] for r in failed_only.json()] == ["Bad SQL"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    other_org = await client.get("/workflows/runs", headers={"Authorization": f"Bearer {token_b}"})
    assert other_org.json() == []


async def test_cross_org_isolation_on_workflow(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    dataset_id = await _upload(client, token_a)
    headers_a = {"Authorization": f"Bearer {token_a}"}

    created = await client.post("/workflows", headers=headers_a, json=_linear_workflow(dataset_id))
    workflow_id = created.json()["id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    assert (await client.get(f"/workflows/{workflow_id}", headers=headers_b)).status_code == 404
    assert (await client.post(f"/workflows/{workflow_id}/run", headers=headers_b)).status_code == 404
    assert (await client.delete(f"/workflows/{workflow_id}", headers=headers_b)).status_code == 404
