from __future__ import annotations

import pandas as pd
import pytest

from app.services import automl_explain_nl
from app.services import automl_templates as templates

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _fake_engine_result(feature_values: dict[str, list], target: list[float], task: str = "regression") -> dict:
    return {
        "pipeline": object(),  # never actually called - explain_model is monkeypatched in these tests
        "meta": {"task": task, "target_column": "scrap_units", "feature_columns": list(feature_values.keys())},
        "explain_X": pd.DataFrame(feature_values),
        "explain_y": pd.Series(target),
    }


def _patch_top_features(monkeypatch, top_features: list[dict]) -> None:
    monkeypatch.setattr(
        automl_explain_nl.automl_engine, "explain_model",
        lambda pipe, meta, X, y, top_k=4: {"top_features": top_features},
    )


def test_two_driver_golden_sentence_and_matched_recommendation(monkeypatch):
    engine_result = _fake_engine_result(
        {"temp": [60, 65, 70, 75, 80, 85, 90, 95], "speed": [40, 42, 44, 46, 48, 50, 52, 54]},
        target=[1, 2, 3, 4, 5, 6, 7, 8],  # rises with both temp and speed
    )
    _patch_top_features(monkeypatch, [
        {"feature": "temp", "importance": 0.6},
        {"feature": "speed", "importance": 0.4},
    ])
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out["explanation_sentence"] == "Temp high + Speed high -> higher predicted scrap."
    assert out["recommendation"] == (
        "Check process temperature control - running hot correlates with more scrap. "
        "Review heater/cooling setpoints and calibration."
    )


def test_one_driver_fallback_sentence(monkeypatch):
    engine_result = _fake_engine_result(
        {"temp": [60, 65, 70, 75, 80, 85, 90, 95]}, target=[1, 2, 3, 4, 5, 6, 7, 8],
    )
    _patch_top_features(monkeypatch, [{"feature": "temp", "importance": 0.6}])
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out["explanation_sentence"] == "Temp high -> higher predicted scrap."
    assert out["recommendation"] is not None and "temperature" in out["recommendation"].lower()


def test_zero_driver_fallback_to_generic_factor_list(monkeypatch):
    engine_result = _fake_engine_result(
        {"temp": [60, 65, 70], "speed": [40, 42, 44], "machine": ["M1", "M2", "M1"]},
        target=[1, 2, 3],
    )
    _patch_top_features(monkeypatch, [])  # no usable importances at all
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out["explanation_sentence"] == "Top factors: Temp, Speed, Machine."
    assert out["recommendation"] is None  # no single top driver to recommend on


def test_no_usable_drivers_and_no_feature_columns_returns_none_none(monkeypatch):
    engine_result = _fake_engine_result({"temp": [60, 65, 70]}, target=[1, 2, 3])
    engine_result["meta"]["feature_columns"] = []  # nothing to fall back to either
    _patch_top_features(monkeypatch, [])
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out == {"explanation_sentence": None, "recommendation": None}


def test_recommendation_falls_back_to_generic_when_no_rule_matches(monkeypatch):
    # "cycle_time" isn't in scrap_prediction's recommendation_rules vocabulary
    # (temp/speed/pressure/humidity/machine) - should hit the generic fallback.
    engine_result = _fake_engine_result(
        {"cycle_time": [10, 20, 30, 40]}, target=[1, 2, 3, 4],
    )
    _patch_top_features(monkeypatch, [{"feature": "cycle_time", "importance": 0.9}])
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out["recommendation"] == "Investigate Cycle Time - it's the strongest predictor."


def test_role_labels_override_friendly_label(monkeypatch):
    engine_result = _fake_engine_result(
        {"vibration_sensor_3": [1, 2, 3, 4]}, target=[10, 20, 30, 40],
    )
    _patch_top_features(monkeypatch, [{"feature": "vibration_sensor_3", "importance": 0.5}])
    template = templates.get_template("scrap_prediction")

    out = automl_explain_nl.explain(engine_result, template, {"vibration_sensor_3": "Vibration Sensor 3"})
    assert out["explanation_sentence"].startswith("Vibration Sensor 3 high")

    # No override supplied - falls back to a title-cased/underscore-replaced label.
    out2 = automl_explain_nl.explain(engine_result, template, {})
    assert out2["explanation_sentence"].startswith("Vibration Sensor 3 high")


def test_classification_task_renders_risk_percentage(monkeypatch):
    # More-negative oee_slope_7d (steeper OEE decline) -> higher predicted
    # failure probability, so the two are perfectly (negatively) correlated.
    class _FakePipe:
        classes_ = [0, 1]

        def predict_proba(self, X):
            import numpy as np
            probs = {-0.1: 0.6, -0.2: 0.7, -0.3: 0.8, -0.4: 0.9}
            p1 = [probs[float(v)] for v in X["oee_slope_7d"]]
            return np.array([[1 - p, p] for p in p1])

    engine_result = {
        "pipeline": _FakePipe(),
        "meta": {"task": "classification", "target_column": "is_failure_next", "feature_columns": ["oee_slope_7d"]},
        "explain_X": pd.DataFrame({"oee_slope_7d": [-0.1, -0.2, -0.3, -0.4]}),
        "explain_y": pd.Series([0, 1, 1, 1]),
    }
    _patch_top_features(monkeypatch, [{"feature": "oee_slope_7d", "importance": 0.9}])
    template = templates.get_template("predictive_maintenance")

    out = automl_explain_nl.explain(engine_result, template, {})

    assert out["explanation_sentence"] == "Oee Slope 7D falling -> 75% risk of machine failure within the next few days."
    assert out["recommendation"] == (
        "OEE is trending down over the last week - inspect for early signs of mechanical wear before it becomes a failure."
    )


def test_explain_never_raises_on_garbage_input():
    class _Boom:
        def __getitem__(self, key):
            raise RuntimeError("boom")

    template = templates.get_template("scrap_prediction")
    assert automl_explain_nl.explain(_Boom(), template, {}) == {"explanation_sentence": None, "recommendation": None}
    assert automl_explain_nl.explain(None, template, {}) == {"explanation_sentence": None, "recommendation": None}
    assert automl_explain_nl.explain({}, template, None) == {"explanation_sentence": None, "recommendation": None}


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_scrap_prediction_run_populates_explanation_and_recommendation(client):
    token = await _register_and_login(client)

    rows = []
    for i in range(40):
        speed = 40 + (i % 8) * 5
        temp = 60 + (i % 5) * 3
        scrap = max(0, round((speed - 40) * 0.15 + (temp - 60) * 0.1))
        rows.append({
            "total_units": 100, "scrap_units": scrap, "machine": "M1" if i % 2 == 0 else "M2",
            "speed": speed, "temp": temp,
        })
    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode()

    up = await client.post("/datasets", headers=_h(token), files={"file": ("scrap.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "Scrap run", "dataset_id": dataset_id,
        "column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units", "machine_col": "machine"},
        "config": {},
    }
    created = await client.post("/automl/templates/scrap_prediction/runs", headers=_h(token), json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=_h(token))
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert isinstance(run["meta"]["explanation_sentence"], str) and run["meta"]["explanation_sentence"]
    assert isinstance(run["meta"]["recommendation"], str) and run["meta"]["recommendation"]
