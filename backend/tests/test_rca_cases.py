from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# M2 is consistently worse than M1 across all 3 days - should surface as an
# RCA evidence candidate (negative delta_oee vs overall).
RCA_CSV = (
    b"date,machine,planned,operating,total,good\n"
    b"2026-01-01,M1,480,460,900,880\n"
    b"2026-01-01,M2,480,300,500,400\n"
    b"2026-01-02,M1,480,470,920,900\n"
    b"2026-01-02,M2,480,280,480,380\n"
    b"2026-01-03,M1,480,465,910,890\n"
    b"2026-01-03,M2,480,290,490,390\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=RCA_CSV):
    res = await client.post("/datasets", files={"file": ("rca.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _create_case(client, token, dataset_id):
    res = await client.post(
        "/rca-cases/cases",
        headers=_h(token),
        json={"title": "M2 underperforming", "problem_statement": "OEE dropped on M2", "dataset_id": dataset_id},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_case_created_with_root_node_and_event(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    assert case["status"] == "open"
    assert case["title"] == "M2 underperforming"

    nodes = await client.get(f"/rca-cases/cases/{case['id']}/nodes", headers=_h(token))
    assert nodes.status_code == 200
    node_list = nodes.json()
    assert len(node_list) == 1
    assert node_list[0]["node_type"] == "problem"

    events = await client.get(f"/rca-cases/cases/{case['id']}/events", headers=_h(token))
    assert events.status_code == 200
    assert any(e["event_type"] == "case_created" for e in events.json())


async def test_evidence_flags_worse_machine(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    res = await client.post(
        f"/rca-cases/cases/{case['id']}/evidence",
        headers=_h(token),
        json={
            "planned_time_col": "planned", "operating_time_col": "operating",
            "total_units_col": "total", "good_units_col": "good",
            "dimension_cols": ["machine"], "min_support_rows": 1,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["candidates_total"] >= 1
    labels = {c["label"] for c in body["candidates"]}
    assert "M2" in labels
    m2 = next(c for c in body["candidates"] if c["label"] == "M2")
    assert m2["delta_oee"] < 0


async def test_pareto_buckets_sum_to_total(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    res = await client.post(
        f"/rca-cases/cases/{case['id']}/pareto",
        headers=_h(token),
        json={"category_col": "machine", "value_col": "planned", "top_n": 15},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["buckets_total"] == 2
    assert body["buckets"][-1]["cumulative_pct"] == pytest.approx(100.0, abs=0.5)


async def test_timeline_returns_window_around_worst_day(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    res = await client.post(
        f"/rca-cases/cases/{case['id']}/timeline",
        headers=_h(token),
        json={
            "date_col": "date", "planned_time_col": "planned", "operating_time_col": "operating",
            "total_units_col": "total", "good_units_col": "good", "window_days": 3,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["events"]) == 3
    assert any(e["is_center"] for e in body["events"])


async def test_node_crud_and_root_cause_marking(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    create = await client.post(
        f"/rca-cases/cases/{case['id']}/nodes",
        headers=_h(token),
        json={"text": "M2 hydraulic leak", "node_type": "cause", "category": "Machine"},
    )
    assert create.status_code == 201, create.text
    node = create.json()

    mark_root = await client.patch(
        f"/rca-cases/cases/{case['id']}/nodes/{node['id']}",
        headers=_h(token),
        json={"node_type": "root_cause", "status": "verified"},
    )
    assert mark_root.status_code == 200
    assert mark_root.json()["node_type"] == "root_cause"

    delete = await client.delete(f"/rca-cases/cases/{case['id']}/nodes/{node['id']}", headers=_h(token))
    assert delete.status_code == 204


async def test_case_status_auto_advances_with_investigation(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)
    assert case["status"] == "open"

    node = await client.post(
        f"/rca-cases/cases/{case['id']}/nodes",
        headers=_h(token),
        json={"text": "M2 hydraulic leak", "node_type": "cause"},
    )
    assert node.status_code == 201

    after_node = await client.get(f"/rca-cases/cases/{case['id']}", headers=_h(token))
    assert after_node.json()["status"] == "investigating"

    action = await client.post(
        f"/rca-cases/cases/{case['id']}/actions", headers=_h(token), json={"description": "Replace seal"}
    )
    assert action.status_code == 201

    after_action = await client.get(f"/rca-cases/cases/{case['id']}", headers=_h(token))
    assert after_action.json()["status"] == "actions_open"

    events = await client.get(f"/rca-cases/cases/{case['id']}/events", headers=_h(token))
    summaries = [e["summary"] for e in events.json()]
    assert "Status changed to investigating" in summaries
    assert "Status changed to actions_open" in summaries


async def test_case_status_auto_advance_respects_manual_override(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    closed = await client.patch(
        f"/rca-cases/cases/{case['id']}", headers=_h(token), json={"status": "closed"}
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    # A case a human already closed shouldn't get silently reopened by adding a
    # late cause node - auto-advance only fires from "open".
    node = await client.post(
        f"/rca-cases/cases/{case['id']}/nodes",
        headers=_h(token),
        json={"text": "Late-added cause", "node_type": "cause"},
    )
    assert node.status_code == 201

    after = await client.get(f"/rca-cases/cases/{case['id']}", headers=_h(token))
    assert after.json()["status"] == "closed"


async def test_action_verify_reports_before_after_oee(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    create = await client.post(
        f"/rca-cases/cases/{case['id']}/actions",
        headers=_h(token),
        json={"description": "Replace hydraulic seal on M2"},
    )
    assert create.status_code == 201, create.text
    action = create.json()
    assert action["status"] == "open"

    mark_done = await client.patch(
        f"/rca-cases/cases/{case['id']}/actions/{action['id']}",
        headers=_h(token),
        json={"status": "done"},
    )
    assert mark_done.status_code == 200
    assert mark_done.json()["completed_at"] is not None

    verify = await client.post(
        f"/rca-cases/cases/{case['id']}/actions/{action['id']}/verify",
        headers=_h(token),
        json={
            "planned_time_col": "planned", "operating_time_col": "operating",
            "total_units_col": "total", "good_units_col": "good", "date_col": "date",
        },
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["status"] == "verified"
    assert "before_oee" in body["verification"]


async def test_verify_rejected_before_action_marked_done(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    case = await _create_case(client, token, dataset_id)

    create = await client.post(
        f"/rca-cases/cases/{case['id']}/actions", headers=_h(token), json={"description": "Fix it"}
    )
    action = create.json()

    verify = await client.post(
        f"/rca-cases/cases/{case['id']}/actions/{action['id']}/verify",
        headers=_h(token),
        json={
            "planned_time_col": "planned", "operating_time_col": "operating",
            "total_units_col": "total", "good_units_col": "good", "date_col": "date",
        },
    )
    assert verify.status_code == 400


async def test_taxonomy_and_trends(client):
    token = await _register_and_login(client)

    taxonomy = await client.get("/rca-cases/taxonomy", headers=_h(token))
    assert taxonomy.status_code == 200
    assert "Machine" in taxonomy.json()

    trends = await client.get("/rca-cases/trends", headers=_h(token))
    assert trends.status_code == 200
    assert trends.json()["total_root_causes"] == 0


async def test_case_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)
    case = await _create_case(client, token_a, dataset_id)

    res = await client.get(f"/rca-cases/cases/{case['id']}", headers=_h(token_b))
    assert res.status_code == 404

    list_res = await client.get("/rca-cases/cases", headers=_h(token_b))
    assert list_res.json() == []
