from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.automl_drift import check_data_drift


def test_check_data_drift_flags_shifted_numeric_feature():
    rng = np.random.default_rng(0)
    train = pd.DataFrame({"temp": rng.normal(0, 1, 500), "line": ["A", "B"] * 250})
    meta = {"feature_columns": ["temp", "line", "gone"], "schema": {"numeric_features": ["temp"]}}

    same = check_data_drift(meta, train, train.copy())
    assert same["overall_severity"] == "low"
    assert same["skipped_features"] == ["gone"]

    shifted = train.assign(temp=train["temp"] + 3)
    out = check_data_drift(meta, train, shifted)
    assert out["overall_severity"] == "high"
    assert out["features"][0]["feature"] == "temp" and out["features"][0]["type"] == "numeric"
