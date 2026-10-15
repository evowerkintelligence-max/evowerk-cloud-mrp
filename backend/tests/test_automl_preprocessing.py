from __future__ import annotations

import pandas as pd

from app.services.automl_preprocessing import (
    _screen_features,
    _split_numeric_categorical,
)


def test_screen_features_drops_constant_id_and_leaky_columns():
    n = 50
    df = pd.DataFrame({
        "row_id": range(n),  # sequential unique ints -> ID-like
        "const": [1] * n,
        "leak": [float(i % 5) for i in range(n)],
        "signal": [float((i * 7) % 11) for i in range(n)],
        "y": [float(i % 5) for i in range(n)],
    })
    kept, notes = _screen_features(df, "y", ["row_id", "const", "leak", "signal"])
    assert kept == ["signal"]
    assert len(notes) == 3


def test_screen_features_never_excludes_everything():
    df = pd.DataFrame({"const": [1] * 30, "y": range(30)})
    kept, notes = _screen_features(df, "y", ["const"])
    assert kept == ["const"]
    assert "skipped screening" in notes[0]


def test_split_numeric_categorical_ignores_missing_columns():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    assert _split_numeric_categorical(df, ["a", "b", "missing"]) == (["a"], ["b"])
