from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import downtime_risk as dr

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

_REASONS = ["breakdown", "changeover", "material_shortage", "planned_maintenance", "quality_hold", "other"]
_BASE_DURATION = {"breakdown": 90, "changeover": 20, "material_shortage": 45, "planned_maintenance": 60, "quality_hold": 15, "other": 30}


def _fixture_df() -> pd.DataFrame:
    rows = []
    for i in range(40):
        reason = _REASONS[i % len(_REASONS)]
        shift = "day" if i % 2 == 0 else "night"
        # Night shift runs a bit longer on average - real, learnable signal.
        duration = _BASE_DURATION[reason] + (10 if shift == "night" else 0) + (i % 3)
        rows.append({
            "machine": "M1" if i % 3 == 0 else "M2", "reason": reason, "shift": shift,
            "duration_min": duration, "planned": reason in ("changeover", "planned_maintenance"),
        })
    return pd.DataFrame(rows)


def test_downtime_risk_registered():
    from app.services import automl_templates as templates
    ids = {t.id for t in templates.list_templates()}
    assert "downtime_risk" in ids


def test_preprocess_uses_exactly_machine_reason_shift_as_features():
    df = _fixture_df()
    config = {"column_map": {
        "machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min",
    }}
    result = dr.preprocess(df, config)
    assert result.skip_ml is False
    assert result.target_col == "duration_min"
    assert set(result.feature_cols) == {"machine", "reason", "shift"}
    assert "planned" not in result.feature_cols


def test_preprocess_requires_all_four_roles():
    df = _fixture_df()
    config = {"column_map": {"machine_col": "machine", "reason_col": "reason"}}
    with pytest.raises(ValueError, match="Missing required column mapping"):
        dr.preprocess(df, config)


def test_postprocess_produces_downtime_summary():
    df = _fixture_df()
    config = {"column_map": {"machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min"}}
    pre = dr.preprocess(df, config)
    from app.services import automl_engine
    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, thoroughness="fast",
    )
    out = dr.postprocess(engine_result, config, pre.context)
    assert out["downtime_summary"]["target_column"] == "duration_min"
    assert out["pipeline"] is not None


@pytest.mark.asyncio
async def test_create_downtime_risk_run_end_to_end(client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    df = _fixture_df()
    csv_bytes = df.to_csv(index=False).encode()
    up = await client.post("/datasets", headers=headers, files={"file": ("downtime.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "Downtime run", "dataset_id": dataset_id,
        "column_map": {"machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min"},
        "config": {},
    }
    created = await client.post("/automl/templates/downtime_risk/runs", headers=headers, json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["meta"]["downtime_summary"]["target_column"] == "duration_min"
