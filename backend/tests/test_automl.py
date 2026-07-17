from __future__ import annotations

import pandas as pd
import pytest

from app.services import automl_engine

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

# y = 1 whenever x1 + x2 > 0, an easily-separable synthetic classification set.
CSV = b"x1,x2,y\n" + b"\n".join(
    f"{x1},{x2},{1 if x1 + x2 > 0 else 0}".encode()
    for x1, x2 in [(i % 7 - 3, (i * 3) % 5 - 2) for i in range(60)]
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="labeled.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


@pytest.mark.asyncio
async def test_classification_run_succeeds_end_to_end(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Predict y",
        "dataset_id": dataset_id,
        "task": "classification",
        "target_column": "y",
        "feature_columns": ["x1", "x2"],
        "thoroughness": "fast",
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    run_id = created.json()["id"]
    # httpx's ASGITransport runs the whole ASGI cycle (including FastAPI
    # BackgroundTasks) inside this await, so the run has already finished by
    # the time this response comes back - no polling needed (see test_workflows.py).

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success"
    assert run["best_model_name"]
    assert run["metric_name"] and run["metric_value"] is not None
    assert len(run["leaderboard"]) >= 1

    explain = await client.get(f"/automl/runs/{run_id}/explain", headers=headers)
    assert explain.status_code == 200
    assert explain.json()["top_features"]

    drift = await client.post(f"/automl/runs/{run_id}/drift-check", headers=headers, json={"dataset_id": dataset_id})
    assert drift.status_code == 200
    assert drift.json()["overall_severity"] in ("low", "moderate", "high")

    deleted = await client.delete(f"/automl/runs/{run_id}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_bad_target_column_marks_run_failed(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Bad target",
        "dataset_id": dataset_id,
        "task": "classification",
        "target_column": "does_not_exist",
        "thoroughness": "fast",
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "failed"
    assert run["error"]


def test_time_budget_seconds_skips_remaining_candidates():
    df = pd.DataFrame({
        "x1": [i % 7 - 3 for i in range(60)],
        "x2": [(i * 3) % 5 - 2 for i in range(60)],
    })
    df["y"] = ((df["x1"] + df["x2"]) > 0).astype(int)

    with pytest.raises(RuntimeError, match="could not train any candidate"):
        automl_engine.train_automl_baseline(
            df, "y", automl_engine.AutoMLTask.classification,
            feature_columns=["x1", "x2"], thoroughness="fast", time_budget_seconds=1e-9,
        )


def _synthetic_classification_df() -> pd.DataFrame:
    df = pd.DataFrame({
        "x1": [i % 7 - 3 for i in range(120)],
        "x2": [(i * 3) % 5 - 2 for i in range(120)],
    })
    df["y"] = ((df["x1"] + df["x2"]) > 0).astype(int)
    return df


def test_bayesian_search_method_trains_and_is_recorded():
    df = _synthetic_classification_df()
    result = automl_engine.train_automl_baseline(
        df, "y", automl_engine.AutoMLTask.classification,
        feature_columns=["x1", "x2"], thoroughness="fast",
        algorithms=["LogisticRegression"], search_method="bayesian",
    )
    assert result["meta"]["model_selection"]["search_method"] == "bayesian"
    assert not any("falling back to random search" in n for n in result["notes"])


def test_explain_model_uses_shap_when_available():
    df = _synthetic_classification_df()
    result = automl_engine.train_automl_baseline(
        df, "y", automl_engine.AutoMLTask.classification,
        feature_columns=["x1", "x2"], thoroughness="fast", algorithms=["LogisticRegression"],
    )
    explanation = automl_engine.explain_model(result["pipeline"], result["meta"], result["explain_X"], result["explain_y"])
    assert "SHAP" in explanation["narrative"]
    assert {f["feature"] for f in explanation["top_features"]} == {"x1", "x2"}
    assert all(f["importance"] is not None and f["importance"] >= 0 for f in explanation["top_features"])


def test_explain_row_returns_prediction_and_contributions():
    df = _synthetic_classification_df()
    result = automl_engine.train_automl_baseline(
        df, "y", automl_engine.AutoMLTask.classification,
        feature_columns=["x1", "x2"], thoroughness="fast", algorithms=["LogisticRegression"],
    )
    explanation = automl_engine.explain_row(
        result["pipeline"], result["meta"], result["explain_X"], result["explain_y"], row={"x1": 2, "x2": 1},
    )
    assert explanation["prediction"] in (0, 1)
    assert explanation["probabilities"] and set(explanation["probabilities"]) == {"0", "1"}
    assert {c["feature"] for c in explanation["contributions"]} == {"x1", "x2"}
    assert explanation["confidence"]["confidence_label"] in ("high", "medium", "low")


def test_explain_row_rejects_forecasting():
    df = pd.DataFrame({"t": pd.date_range("2024-01-01", periods=60, freq="D"), "y": range(60)})
    result = automl_engine.train_automl_baseline(
        df, "y", automl_engine.AutoMLTask.forecasting, feature_columns=["y"],
        time_column="t", thoroughness="fast", algorithms=["Ridge"],
    )
    with pytest.raises(ValueError, match="not available for task"):
        automl_engine.explain_row(result["pipeline"], result["meta"], result["explain_X"], result["explain_y"], row={})


def test_random_forest_trains_on_string_typed_target():
    # Mirrors what a CSV upload actually produces: dataframe_for() loads every
    # column as a string (see app/services/datasets.py), so a numeric-looking
    # label column like "0"/"1" arrives as a non-numeric dtype, not int. Before
    # the fix, RandomForestClassifier/ExtraTreesClassifier's class_weight=
    # "balanced" crashed on this with "The classes, [0, 1], are not in class_weight".
    df = pd.DataFrame({
        "x1": [str(i % 7 - 3) for i in range(120)],
        "x2": [str((i * 3) % 5 - 2) for i in range(120)],
        "region": ["east" if i % 2 == 0 else "west" for i in range(120)],
    })
    df["y"] = [str(int((int(a) + int(b)) > 0)) for a, b in zip(df["x1"], df["x2"])]
    assert not pd.api.types.is_numeric_dtype(df["y"])

    for algo in ["RandomForestClassifier", "ExtraTreesClassifier"]:
        result = automl_engine.train_automl_baseline(
            df, "y", automl_engine.AutoMLTask.classification,
            feature_columns=["x1", "x2", "region"], thoroughness="fast", algorithms=[algo],
        )
        assert result["meta"]["model_name"] == algo


def test_true_string_class_labels_are_not_coerced():
    df = pd.DataFrame({"x1": [str(i % 7 - 3) for i in range(80)]})
    df["label"] = ["yes" if int(v) > 0 else "no" for v in df["x1"]]
    result = automl_engine.train_automl_baseline(
        df, "label", automl_engine.AutoMLTask.classification,
        feature_columns=["x1"], thoroughness="fast", algorithms=["RandomForestClassifier"],
    )
    assert result["meta"]["model_name"] == "RandomForestClassifier"


def _forecast_csv() -> bytes:
    """40 consecutive days of a simple upward-trending value - enough history
    for the forecasting engine's 14-day lag/rolling warmup plus a real
    train/test split."""
    import datetime

    start = datetime.date(2026, 1, 1)
    rows = ["date,value"]
    for i in range(40):
        d = start + datetime.timedelta(days=i)
        rows.append(f"{d.isoformat()},{100 + i}")
    return ("\n".join(rows) + "\n").encode()


@pytest.mark.asyncio
async def test_forecasting_run_succeeds_on_csv_sourced_string_columns(client):
    """Regression test for the string-vs-numeric TypeError: CSV-sourced datasets
    load every column as a string (see app.services.datasets.dataframe_for), so
    a forecasting run's target column arrives as dtype=object. Training must
    coerce it before doing lag/rolling arithmetic, not just before the final
    train/test split."""
    token = await _register_and_login(client, email="forecast@example.com", org_name="ForecastOrg")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=_forecast_csv(), filename="forecast.csv")

    body = {
        "name": "Forecast value",
        "dataset_id": dataset_id,
        "task": "forecasting",
        "target_column": "value",
        "feature_columns": [],
        "thoroughness": "fast",
        "time_column": "date",
        "horizon": 1,
        "forecast_strategy": "direct",
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["best_model_name"]
