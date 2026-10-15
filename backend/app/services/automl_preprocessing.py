"""Feature screening (data-quality / leakage heuristics) and the sklearn
preprocessing ColumnTransformer for automl_engine. Split out of
automl_engine.py; pure functions over in-memory pandas objects."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, TargetEncoder

# =========================================================
# Feature screening + preprocessing
# =========================================================


def _looks_like_numeric_id(col: pd.Series) -> bool:
    """True only for columns that look like a literal row index / auto-increment
    key: integer-valued, all-unique, and monotonic. Deliberately conservative —
    continuous numeric features are naturally all-unique but are NOT ids."""
    s = col.dropna()
    if len(s) < 20:
        return False
    if int(s.nunique()) != len(s):
        return False
    try:
        vals = s.astype(float).to_numpy()
    except Exception:
        return False
    if not np.all(vals == np.floor(vals)):
        return False
    return bool(s.is_monotonic_increasing or s.is_monotonic_decreasing)


def _screen_features(df: pd.DataFrame, target_col: str, feat_cols: list[str]) -> tuple[list[str], list[str]]:
    """Heuristic data-quality / leakage screen, applied only to auto-inferred
    feature sets (explicit user selections bypass this). Drops zero-variance,
    ID-like, and near-perfectly-target-correlated numeric columns."""
    kept: list[str] = []
    notes: list[str] = []
    n = len(df)
    if n == 0:
        return feat_cols, notes

    target_numeric: pd.Series | None = None
    try:
        if pd.api.types.is_numeric_dtype(df[target_col]):
            target_numeric = df[target_col].astype(float)
        else:
            target_numeric = df[target_col].astype("category").cat.codes.astype(float)
    except Exception:
        target_numeric = None

    for c in feat_cols:
        if c not in df.columns:
            continue
        col = df[c]
        nunique = int(col.nunique(dropna=True))
        if nunique <= 1:
            notes.append(f"Excluded '{c}' (constant column — no variance).")
            continue

        is_numeric = pd.api.types.is_numeric_dtype(col)
        if n >= 20 and not is_numeric and (nunique / n) > 0.95:
            notes.append(f"Excluded '{c}' (ID-like column, {nunique / n * 100:.1f}% unique values).")
            continue
        if is_numeric and _looks_like_numeric_id(col):
            notes.append(f"Excluded '{c}' (ID-like column — sequential unique integers, e.g. a row index).")
            continue

        if target_numeric is not None and is_numeric:
            try:
                corr = float(col.astype(float).corr(target_numeric))
                if corr == corr:
                    acorr = abs(corr)
                    if acorr > 0.999:
                        notes.append(
                            f"Excluded '{c}' (correlation {corr:.3f} with target — near-perfect duplicate, "
                            "almost certainly leakage)."
                        )
                        continue
                    if acorr > 0.98:
                        notes.append(
                            f"Kept '{c}' but it correlates {corr:.3f} with the target — verify it is a real "
                            "predictor, not leakage."
                        )
            except Exception:
                pass

        kept.append(c)

    if not kept and feat_cols:
        return feat_cols, ["Feature screening would have excluded every column — skipped screening."]
    return kept, notes


def _split_numeric_categorical(df: pd.DataFrame, cols: list[str]) -> tuple[list[str], list[str]]:
    num_cols: list[str] = []
    cat_cols: list[str] = []
    for c in cols:
        if c not in df.columns:
            continue
        (num_cols if pd.api.types.is_numeric_dtype(df[c]) else cat_cols).append(c)
    return num_cols, cat_cols


def _make_preprocessor(
    num_cols: list[str], cat_cols_low: list[str], cat_cols_high: list[str], target_type: str = "auto"
) -> ColumnTransformer:
    numeric = Pipeline(steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())])
    # sparse_output=False keeps the matrix dense — required by HistGradientBoosting
    # (rejects sparse input) and friendlier to KNN/SVM; safe since only low-cardinality
    # columns are one-hot encoded (high-cardinality routes through TargetEncoder).
    categorical_low = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    # target_type="continuous" for regression/forecasting is critical: with the
    # default "auto", an integer-valued regression target gets mis-detected as
    # multiclass, exploding a high-cardinality column into one column per value.
    categorical_high = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("target_enc", TargetEncoder(target_type=target_type)),
        ]
    )
    transformers: list[tuple[str, Any, list[str]]] = [("num", numeric, num_cols)]
    if cat_cols_low:
        transformers.append(("cat_low", categorical_low, cat_cols_low))
    if cat_cols_high:
        transformers.append(("cat_high", categorical_high, cat_cols_high))
    return ColumnTransformer(transformers=transformers, remainder="drop")
