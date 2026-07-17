from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services import automl_rare_event as rare


def _fixture_df(n_normal: int, n_positive: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    normal = pd.DataFrame({
        "x": rng.normal(0, 1, n_normal), "y": rng.normal(0, 1, n_normal), "flag": 0,
    })
    positive = pd.DataFrame({
        "x": rng.normal(6, 1, n_positive), "y": rng.normal(6, 1, n_positive), "flag": 1,
    })
    return pd.concat([normal, positive], ignore_index=True)


def test_boundary_19_positives_routes_to_anomaly():
    df = _fixture_df(n_normal=60, n_positive=19)
    result = rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)
    assert result.skip_ml is True
    assert result.context["rare_event_routing"] == "anomaly"


def test_boundary_20_positives_routes_to_classification():
    df = _fixture_df(n_normal=60, n_positive=20)
    result = rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)
    assert result.skip_ml is False
    assert result.task == "classification"
    assert result.context["rare_event_routing"] == "classification"


def test_both_routes_return_the_same_result_shape():
    anomaly = rare.route_rare_event(_fixture_df(60, 10), "flag", ["x", "y"], min_positive_examples=20)
    classification = rare.route_rare_event(_fixture_df(60, 25), "flag", ["x", "y"], min_positive_examples=20)

    for result in (anomaly, classification):
        assert hasattr(result, "skip_ml")
        assert hasattr(result, "notes") and isinstance(result.notes, list) and result.notes
        assert hasattr(result, "context") and isinstance(result.context, dict)
        assert "rare_event_routing" in result.context
        assert result.context["rare_event_routing"] in ("anomaly", "classification")
        assert "target_col" in result.context

    # Anomaly path stashes an engine_result-shaped dict in context so the
    # template's postprocess() doesn't need to special-case the two routes.
    engine_result = anomaly.context["engine_result"]
    for key in ("pipeline", "leaderboard", "meta", "explain_X", "explain_y", "notes"):
        assert key in engine_result
    assert engine_result["leaderboard"][0]["model_name"] == "IsolationForest"
    assert engine_result["meta"]["model_name"] == "IsolationForest"

    # Classification path leaves engine_result-building to
    # automl_engine.train_automl_baseline() itself (called by automl_runner.py).
    assert classification.df is not None
    assert classification.feature_cols == ["x", "y"]


def test_zero_positive_examples_fails_explicitly():
    df = _fixture_df(n_normal=60, n_positive=0)
    with pytest.raises(ValueError, match="Zero positive examples"):
        rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)


def test_too_few_normal_rows_fails_explicitly():
    df = _fixture_df(n_normal=30, n_positive=5)  # < MIN_NORMAL_ROWS (50)
    with pytest.raises(ValueError, match="not enough data to trust any model"):
        rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)


def test_anomaly_scores_are_bounded_and_flag_the_outlier_group_higher():
    df = _fixture_df(n_normal=60, n_positive=10)
    result = rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)
    assert result.skip_ml is True
    scores = result.context["anomaly_scores"]
    assert all(0.0 <= s <= 1.0 for s in scores)
    normal_scores = [s for s, flag in zip(scores, df["flag"]) if flag == 0]
    positive_scores = [s for s, flag in zip(scores, df["flag"]) if flag == 1]
    assert sum(positive_scores) / len(positive_scores) > sum(normal_scores) / len(normal_scores)


def test_severe_imbalance_notes_the_metric_gap_without_touching_automl_engine():
    # 25 positives, ~600 normal rows -> ratio well above the 10:1 switch threshold.
    df = _fixture_df(n_normal=600, n_positive=25)
    result = rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20)
    assert result.skip_ml is False
    assert any("severe class imbalance" in n.lower() for n in result.notes)
    assert any("automl_engine.py" in n for n in result.notes)


def test_min_normal_rows_and_min_positive_examples_are_overridable():
    df = _fixture_df(n_normal=12, n_positive=3)
    with pytest.raises(ValueError):
        rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=20, min_normal_rows=50)
    # Lowering both floors makes the same data routable.
    result = rare.route_rare_event(df, "flag", ["x", "y"], min_positive_examples=5, min_normal_rows=10)
    assert result.skip_ml is True
