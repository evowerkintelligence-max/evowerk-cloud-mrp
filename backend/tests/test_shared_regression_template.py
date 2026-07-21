from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "units": [100, 110, 90, 105, 95, 120, 80, 100, 115, 90] * 3,
        "scrap": [2, 3, 1, 4, 2, 5, 1, 2, 3, 2] * 3,
        "reason": ["tooling", "tooling", "cal", "tooling", "cal", "tooling", "cal", "tooling", "cal", "tooling"] * 3,
        "machine": ["M1", "M2"] * 15,
        "speed": [50, 55, 45, 52, 48, 60, 40, 50, 58, 47] * 3,
    })


def test_preprocess_all_other_columns_mode_uses_every_unmapped_column_as_a_feature():
    df = _df()
    preprocess = make_simple_regression_preprocess(
        target_role="scrap_col", required_roles=["units_col", "scrap_col"],
        excluded_from_features=["units_col", "reason_col"],
    )
    config = {"column_map": {"units_col": "units", "scrap_col": "scrap", "reason_col": "reason", "machine_col": "machine"}}
    result = preprocess(df, config)
    assert result.skip_ml is False
    assert result.task == "regression"
    assert result.target_col == "scrap"
    # "units" and "reason" excluded; "machine" and "speed" (unmapped by any
    # role at all) both survive since explicit_feature_roles=None means "all
    # other columns."
    assert set(result.feature_cols) == {"machine", "speed"}
    assert len(result.df) == 30


def test_preprocess_explicit_feature_roles_mode_uses_only_named_columns():
    df = pd.DataFrame({
        "dur": [12.0, 45.0, 8.0, 30.0, 15.0, 50.0, 10.0, 22.0, 18.0, 40.0] * 3,
        "machine": ["M1", "M2"] * 15,
        "reason": ["breakdown", "changeover"] * 15,
        "shift": ["day", "night"] * 15,
        "unrelated": ["x"] * 30,
    })
    preprocess = make_simple_regression_preprocess(
        target_role="duration_col", required_roles=["machine_col", "reason_col", "shift_col", "duration_col"],
        explicit_feature_roles=["machine_col", "reason_col", "shift_col"],
    )
    config = {"column_map": {"duration_col": "dur", "machine_col": "machine", "reason_col": "reason", "shift_col": "shift"}}
    result = preprocess(df, config)
    assert set(result.feature_cols) == {"machine", "reason", "shift"}
    assert "unrelated" not in result.feature_cols


def test_preprocess_raises_on_missing_required_role():
    df = _df()
    preprocess = make_simple_regression_preprocess(target_role="scrap_col", required_roles=["units_col", "scrap_col"])
    with pytest.raises(ValueError, match="Missing required column mapping"):
        preprocess(df, {"column_map": {"units_col": "units"}})


def test_preprocess_raises_on_mapped_column_not_in_dataset():
    df = _df()
    preprocess = make_simple_regression_preprocess(target_role="scrap_col", required_roles=["units_col", "scrap_col"])
    with pytest.raises(ValueError, match="not found in dataset"):
        preprocess(df, {"column_map": {"units_col": "units", "scrap_col": "does_not_exist"}})


def test_preprocess_raises_when_too_few_rows_have_a_valid_target():
    df = pd.DataFrame({"units": [100, 110, 90], "scrap": [2, None, 1]})
    preprocess = make_simple_regression_preprocess(
        target_role="scrap_col", required_roles=["units_col", "scrap_col"], min_rows=20,
    )
    with pytest.raises(ValueError, match="Not enough rows"):
        preprocess(df, {"column_map": {"units_col": "units", "scrap_col": "scrap"}})


def test_postprocess_summarizes_engine_result_under_summary_key():
    postprocess = make_simple_regression_postprocess(summary_key="scrap_summary")
    engine_result = {
        "pipeline": "fake-pipeline", "explain_X": "fake-x", "explain_y": "fake-y",
        "leaderboard": [{"rank": 1}],
        "meta": {"model_name": "Ridge", "metric_name": "rmse", "metric_value": 1.23, "rows_used": 42},
    }
    out = postprocess(engine_result, {}, {"target_col": "scrap"})
    assert out["scrap_summary"] == {
        "target_column": "scrap", "best_model": "Ridge", "metric_name": "rmse",
        "metric_value": 1.23, "rows_used": 42,
    }
    assert out["pipeline"] == "fake-pipeline"
    assert out["best_model_name"] == "Ridge"


def test_postprocess_raises_if_engine_result_is_none():
    postprocess = make_simple_regression_postprocess(summary_key="x")
    with pytest.raises(RuntimeError, match="never set skip_ml"):
        postprocess(None, {}, {"target_col": "y"})
