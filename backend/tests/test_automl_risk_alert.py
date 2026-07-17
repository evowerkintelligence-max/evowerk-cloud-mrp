from __future__ import annotations

import uuid

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.models.alert import AlertRule
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import alerts


def _fake_rule(config: dict) -> AlertRule:
    return AlertRule(
        id=uuid.uuid4(), org_id=uuid.uuid4(), created_by=uuid.uuid4(), name="Risk alert",
        type="automl_risk", severity="warning", enabled=True, dataset_id=uuid.uuid4(), config=config,
    )


def _fitted_pipeline_and_model_path(tmp_path, feature_cols: list[str]) -> str:
    X = pd.DataFrame({c: [0, 1, 0, 1, 0, 1, 0, 1] for c in feature_cols})
    y = [0, 1, 0, 1, 0, 1, 0, 1]
    pipe = Pipeline([("model", LogisticRegression())])
    pipe.fit(X, y)
    path = tmp_path / "model.joblib"
    joblib.dump(pipe, path)
    return str(path)


@pytest.mark.asyncio
async def test_eval_automl_risk_uses_meta_feature_columns_when_present(tmp_path, monkeypatch):
    model_path = _fitted_pipeline_and_model_path(tmp_path, ["x1", "x2"])
    run = AutoMLRun(
        id=uuid.uuid4(), org_id=uuid.uuid4(), dataset_id=uuid.uuid4(), created_by=uuid.uuid4(),
        name="r", task="classification", target_column="y", status="success", model_path=model_path,
        meta={"feature_columns": ["x1", "x2"], "task": "classification"},
    )
    rule = _fake_rule({"run_id": str(run.id), "threshold": 0.5, "offender_col": "machine"})
    # _missing_columns() checks offender_col against dataset.columns (a list
    # of {"name": ...} dicts), so this fixture must actually list "machine" -
    # an empty columns=[] would make the evaluator return early with a
    # "column not found" warning instead of exercising the scoring path.
    dataset = Dataset(
        id=uuid.uuid4(), org_id=run.org_id, name="d", original_filename="d.csv", fmt="csv", storage_ext="csv",
        created_by=uuid.uuid4(), row_count=4,
        columns=[{"name": "x1"}, {"name": "x2"}, {"name": "machine"}, {"name": "y"}],
    )

    df = pd.DataFrame({"x1": [1, 1, 0, 0], "x2": [1, 1, 0, 0], "machine": ["M1", "M2", "M3", "M4"], "y": [1, 1, 0, 0]})
    monkeypatch.setattr("app.services.datasets.dataframe_for", lambda ds: df)

    class _FakeResult:
        def scalar_one_or_none(self):
            return run

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), run.org_id, rule, dataset)
    assert trigger.type == "automl_risk"
    assert set(trigger.offenders) <= {"M1", "M2", "M3", "M4"}


@pytest.mark.asyncio
async def test_eval_automl_risk_falls_back_to_target_column_when_meta_has_no_feature_columns(tmp_path, monkeypatch):
    # Mirrors a template run: meta has no "feature_columns" key at all, only
    # run.target_column is reliably set (see the plan's design note).
    model_path = _fitted_pipeline_and_model_path(tmp_path, ["x1", "x2"])
    run = AutoMLRun(
        id=uuid.uuid4(), org_id=uuid.uuid4(), dataset_id=uuid.uuid4(), created_by=uuid.uuid4(),
        name="r", task="classification", target_column="y", status="success", model_path=model_path,
        meta={"predictive_result": {"marker": "ok"}},
    )
    rule = _fake_rule({"run_id": str(run.id), "threshold": 0.5})
    dataset = Dataset(id=uuid.uuid4(), org_id=run.org_id, name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=4, columns=[])

    df = pd.DataFrame({"x1": [1, 1, 0, 0], "x2": [1, 1, 0, 0], "y": [1, 1, 0, 0]})
    monkeypatch.setattr("app.services.datasets.dataframe_for", lambda ds: df)

    class _FakeResult:
        def scalar_one_or_none(self):
            return run

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), run.org_id, rule, dataset)
    # No crash, no "unknown column" error - feature set correctly fell back
    # to "every column except target_column" (x1, x2), not an empty set.
    assert trigger.type == "automl_risk"
    assert "Error" not in trigger.message


@pytest.mark.asyncio
async def test_eval_automl_risk_reports_missing_run_id():
    rule = _fake_rule({})
    dataset = Dataset(id=uuid.uuid4(), org_id=uuid.uuid4(), name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=0, columns=[])

    class _FakeDB:
        async def execute(self, *a, **kw):
            raise AssertionError("should not query the DB when run_id is missing")

    trigger = await alerts._eval_automl_risk(_FakeDB(), uuid.uuid4(), rule, dataset)
    assert trigger.triggered is False
    assert "No AutoML run configured" in trigger.message


@pytest.mark.asyncio
async def test_eval_automl_risk_reports_run_not_found():
    rule = _fake_rule({"run_id": str(uuid.uuid4())})
    dataset = Dataset(id=uuid.uuid4(), org_id=uuid.uuid4(), name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=0, columns=[])

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), uuid.uuid4(), rule, dataset)
    assert trigger.triggered is False
    assert "not found" in trigger.message.lower()
