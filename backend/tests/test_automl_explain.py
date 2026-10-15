from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from app.services.automl_explain import explain_model, partial_dependence_data


def test_explain_model_without_sample_lists_features_without_importance():
    meta = {"model_name": "Ridge", "metric_name": "rmse", "metric_value": 1.5, "feature_columns": ["a", "b"]}
    out = explain_model(None, meta, None, None)
    assert out["top_features"] == [{"feature": "a", "importance": None}, {"feature": "b", "importance": None}]
    assert "explain sample is missing" in out["narrative"]


def test_partial_dependence_tracks_a_linear_model():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=60), "b": rng.normal(size=60)})
    y = 3 * X["a"] - X["b"]
    pipe = LinearRegression().fit(X, y)
    out = partial_dependence_data(pipe, {"task": "regression", "feature_columns": ["a", "b"]}, X, "a", grid_size=5)
    assert out["is_numeric"] and len(out["grid"]) == 5
    assert np.all(np.diff(out["response"]) > 0)  # positive coefficient -> rising response
