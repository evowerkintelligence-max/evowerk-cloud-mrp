from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

from app.services.narratives import build_narrative

PASSWORD = TEST_PASSWORD


def test_kpi_run_ranks_by_magnitude_and_formats_bullets():
    results = {
        "a1": {
            "type": "kpi_run",
            "ok": True,
            "payload": {"results": [
                {"kpi_id": "scrap_rate", "value": 4.2},
                {"kpi_id": "throughput", "value": 12500},
            ]},
        },
    }
    text = build_narrative(question="How are we doing?", results_by_action_id=results, style="bullet")
    assert "Top KPI readings:" in text
    lines = text.splitlines()
    throughput_idx = next(i for i, line in enumerate(lines) if "throughput" in line)
    scrap_idx = next(i for i, line in enumerate(lines) if "scrap_rate" in line)
    assert throughput_idx < scrap_idx  # ranked by magnitude, 12500 > 4.2
    assert "12.50K" in text


def test_failed_action_reported_as_bullet_not_crash():
    results = {"a1": {"type": "kpi_run", "ok": False, "error": "dataset not found"}}
    text = build_narrative(question="What broke?", results_by_action_id=results, style="bullet")
    assert "[a1] kpi_run: dataset not found" in text


def test_compare_deltas_formatted_with_percent():
    results = {
        "cmp": {
            "type": "compare_runs",
            "ok": True,
            "payload": {"kpi_deltas": {"scrap_rate": {"a": 10.0, "b": 12.0}}},
        }
    }
    text = build_narrative(question="vs last month", results_by_action_id=results, style="bullet")
    assert "scrap_rate: 12.00 (+20.0% vs 10.00)" in text


def test_forecast_trend_direction():
    results = {
        "f1": {
            "type": "forecast_kpi",
            "ok": True,
            "payload": {"kpi_key": "demand", "points": [{"yhat": 100}, {"yhat": 150}], "horizon": 7, "method_used": "ses"},
        }
    }
    text = build_narrative(question="what's next", results_by_action_id=results, style="bullet")
    assert "Forecast (demand): up over 7 steps" in text


def test_source_action_ids_filters_results():
    results = {
        "keep": {"type": "kpi_run", "ok": True, "payload": {"results": [{"kpi_id": "x", "value": 1}]}},
        "drop": {"type": "kpi_run", "ok": False, "error": "should not appear"},
    }
    text = build_narrative(question="q", results_by_action_id=results, style="bullet", source_action_ids=["keep"])
    assert "should not appear" not in text


def test_style_variants_all_produce_nonempty_text():
    results = {"a1": {"type": "kpi_run", "ok": True, "payload": {"results": [{"kpi_id": "x", "value": 1}]}}}
    for style in ("bullet", "paragraph", "mixed"):
        text = build_narrative(question="q", results_by_action_id=results, style=style)
        assert text.strip()


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_narrative_endpoint(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/ai/narrative",
        headers=headers,
        json={
            "question": "How is scrap trending?",
            "results_by_action_id": {
                "a1": {"type": "kpi_run", "ok": True, "payload": {"results": [{"kpi_id": "scrap_rate", "value": 4.2}]}},
            },
            "style": "bullet",
        },
    )
    assert res.status_code == 200, res.text
    assert "scrap_rate" in res.json()["narrative"]


@pytest.mark.asyncio
async def test_narrative_endpoint_requires_question(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/ai/narrative", headers=headers, json={"question": "  ", "results_by_action_id": {}})
    assert res.status_code == 400
