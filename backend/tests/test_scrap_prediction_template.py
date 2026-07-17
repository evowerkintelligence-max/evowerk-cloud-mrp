from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import scrap_prediction as sp

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _fixture_df() -> pd.DataFrame:
    rows = []
    for i in range(40):
        speed = 40 + (i % 8) * 5
        temp = 60 + (i % 5) * 3
        units = 100
        # Higher speed/temp -> more scrap, so there's real signal to learn.
        scrap = max(0, round((speed - 40) * 0.15 + (temp - 60) * 0.1))
        rows.append({
            "date": f"2026-01-{(i % 28) + 1:02d}", "machine": "M1" if i % 2 == 0 else "M2",
            "total_units": units, "scrap_units": scrap, "reason": "tooling" if scrap > 0 else "",
            "speed": speed, "temp": temp,
        })
    return pd.DataFrame(rows)


def test_scrap_prediction_registered():
    from app.services import automl_templates as templates
    ids = {t.id for t in templates.list_templates()}
    assert "scrap_prediction" in ids


def test_preprocess_excludes_total_units_and_reason_as_features():
    df = _fixture_df()
    config = {"column_map": {
        "total_units_col": "total_units", "scrap_col": "scrap_units",
        "reason_col": "reason", "machine_col": "machine", "date_col": "date",
    }}
    result = sp.preprocess(df, config)
    assert result.skip_ml is False
    assert result.target_col == "scrap_units"
    assert "total_units" not in result.feature_cols
    assert "reason" not in result.feature_cols
    assert {"machine", "date", "speed", "temp"} <= set(result.feature_cols)


def test_preprocess_only_requires_total_units_and_scrap():
    df = _fixture_df()
    config = {"column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units"}}
    result = sp.preprocess(df, config)
    assert result.skip_ml is False


def test_postprocess_produces_scrap_summary():
    df = _fixture_df()
    config = {"column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units", "machine_col": "machine"}}
    pre = sp.preprocess(df, config)
    from app.services import automl_engine
    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, thoroughness="fast",
    )
    out = sp.postprocess(engine_result, config, pre.context)
    assert out["scrap_summary"]["target_column"] == "scrap_units"
    assert out["pipeline"] is not None


@pytest.mark.asyncio
async def test_create_scrap_prediction_run_end_to_end(client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    df = _fixture_df()
    csv_bytes = df.to_csv(index=False).encode()
    up = await client.post("/datasets", headers=headers, files={"file": ("scrap.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "Scrap run", "dataset_id": dataset_id,
        "column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units", "machine_col": "machine"},
        "config": {},
    }
    created = await client.post("/automl/templates/scrap_prediction/runs", headers=headers, json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["meta"]["scrap_summary"]["target_column"] == "scrap_units"
