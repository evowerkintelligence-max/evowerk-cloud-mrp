from __future__ import annotations

import pytest

from tests._report_builder_helpers import (
    _register_and_login,
    _upload,
    _create_chart,
    _report_body,
)

pytestmark = pytest.mark.asyncio


async def test_narrate_custom_report_falls_back_without_llm_key(client):
    """No GEMINI_API_KEY is configured in tests, so narration always takes the
    deterministic fallback path - a real assertion on grounded, non-empty
    text without needing to mock the LLM."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)
    chart_id = await _create_chart(client, token, dataset_id)

    created = await client.post("/reports/custom", headers=headers, json=_report_body(dataset_id, chart_id))
    report_id = created.json()["id"]

    res = await client.post(f"/reports/custom/{report_id}/narrate", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["executive_summary"]
    assert len(body["sections"]) == 5
    assert body["sections"][0]["title"] == "Overview"
    assert all(s["narrative"] for s in body["sections"])


async def test_narrate_custom_report_rejects_unknown_style(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Empty", "sections": [{"type": "text", "text": "hi"}]},
    )
    report_id = created.json()["id"]

    res = await client.post(
        f"/reports/custom/{report_id}/narrate", params={"style": "haiku"}, headers=headers
    )
    assert res.status_code == 400


async def test_narrate_custom_report_cross_org_isolation(client):
    token_a = await _register_and_login(client, email="na@example.com", org_name="NarrateOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    created = await client.post(
        "/reports/custom", headers=headers_a,
        json={"name": "OrgA report", "sections": [{"type": "text", "text": "secret"}]},
    )
    report_id = created.json()["id"]

    token_b = await _register_and_login(client, email="nb@example.com", org_name="NarrateOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    res = await client.post(f"/reports/custom/{report_id}/narrate", headers=headers_b)
    assert res.status_code == 404


async def test_engine_section_resolves_oee_into_summary_and_breakdown_tables(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(
        client, token,
        content=b"planned,operating,total,good,machine\n480,420,1000,950,M1\n480,400,900,850,M2\n",
        filename="oee.csv",
    )

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "OEE capture",
            "sections": [{
                "type": "engine", "title": "OEE",
                "engine_path": "/analytics/oee",
                "engine_body": {
                    "dataset_id": dataset_id,
                    "planned_time_col": "planned", "operating_time_col": "operating",
                    "total_units_col": "total", "good_units_col": "good",
                    "machine_col": "machine",
                },
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    # a "Summary" table (scalar KPI fields) and a "Machines" table (the
    # list[OeeMachineResult] field) should both have rendered as sections
    assert b"OEE" in res.content
    assert b"M1" in res.content
    assert b"M2" in res.content


async def test_engine_section_rejects_unknown_path(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad engine",
            "sections": [{"type": "engine", "engine_path": "/analytics/not-a-real-engine", "engine_body": {}}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_flatten_response_to_sections_scalars_and_lists():
    from app.services.reports import _flatten_response_to_sections

    sections = _flatten_response_to_sections("Test", {
        "oee": 72.5,
        "performance_estimated": False,
        "machines": [{"machine": "M1", "oee": 70.0}, {"machine": "M2", "oee": 75.0}],
        "revenue_impact": {"unit_price": 2.5, "total_lost": 100.0, "per_machine": [{"machine": "M1", "total_lost": 50.0}]},
        "trend": [],
    })

    titles = [s.title for s in sections]
    assert "Test — Summary" in titles
    assert "Test — Machines" in titles
    assert "Test — Revenue Impact — Per Machine" in titles
    # "trend" was an empty list - no section for it
    assert not any("Trend" in t for t in titles)

    summary = next(s for s in sections if s.title == "Test — Summary")
    summary_labels = [row[0] for row in summary.rows]
    assert "Oee" in summary_labels
    assert "Performance Estimated" in summary_labels
    # a nested dict's own scalar fields join the summary, prefixed
    assert any("Revenue Impact" in label for label in summary_labels)


async def test_rca_case_section_renders_causes_and_actions(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    case = await client.post(
        "/rca-cases/cases", headers=headers,
        json={"dataset_id": dataset_id, "title": "Line 3 OEE drop", "problem_statement": "OEE fell 15pts"},
    )
    assert case.status_code == 201
    case_id = case.json()["id"]

    node = await client.post(
        f"/rca-cases/cases/{case_id}/nodes", headers=headers,
        json={"text": "Changeover took too long", "node_type": "cause"},
    )
    assert node.status_code == 201

    action = await client.post(
        f"/rca-cases/cases/{case_id}/actions", headers=headers,
        json={"description": "Standardize changeover checklist"},
    )
    assert action.status_code == 201

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "RCA case capture", "sections": [{"type": "rca_case", "case_id": case_id}]},
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Changeover took too long" in res.content
    assert b"Standardize changeover checklist" in res.content


async def test_rca_case_section_404s_on_unknown_case(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad case",
            "sections": [{"type": "rca_case", "case_id": "00000000-0000-0000-0000-000000000000"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 404
