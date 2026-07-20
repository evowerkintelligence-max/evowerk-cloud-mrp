"""Shared rare-event / severe-imbalance routing, sitting between a
template's preprocess() and automl_engine.train_automl_baseline() - see
docs/superpowers/specs/2026-07-17-manufacturing-automl-design.md,
"Rare-event / imbalance handling". Any template deriving a binary "did the
bad thing happen" target calls route_rare_event() to decide whether there's
enough positive-class signal to train a supervised classifier, or whether to
fall back to unsupervised anomaly scoring (IsolationForest fit on the
majority "normal" rows).

The decision_function-squash-to-probability trick below follows the same
pattern already used by predictive_maintenance.py's own anomaly fallback
(see _fit_anomaly_model/_anomaly_score there) - not imported directly since
that one is wired to predictive_maintenance's specific rolling-window
feature set and per-machine last-window lookup, but the squashing math is
duplicated here in a template-agnostic form so any template can reuse it.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.services.automl_templates import PreprocessResult

MIN_NORMAL_ROWS = 50
_IMBALANCE_METRIC_SWITCH_RATIO = 10.0


def route_rare_event(
    df: pd.DataFrame,
    target_col: str,
    feature_cols: list[str],
    *,
    min_positive_examples: int = 20,
    min_normal_rows: int = MIN_NORMAL_ROWS,
    notes: list[str] | None = None,
    context: dict[str, Any] | None = None,
    random_state: int = 0,
) -> PreprocessResult:
    """`df[target_col]` must already be a derived 0/1 column. Returns a
    PreprocessResult with the same shape regardless of which path ran:

    - >= min_positive_examples positives: skip_ml=False, task="classification",
      df/target_col/feature_cols set as usual - automl_runner.py proceeds
      into automl_engine.train_automl_baseline() exactly like today.
    - < min_positive_examples positives (but >= min_normal_rows negative/
      "normal" rows): skip_ml=True, with a ready-to-use engine_result-shaped
      dict (pipeline/leaderboard/meta/explain_X/explain_y/notes) stashed in
      context["engine_result"] - automl_runner.py calls
      template.postprocess(None, config, context) directly, and the
      template's postprocess() picks the anomaly result up from context
      instead of the (None) engine_result parameter.

    Hard failures (no PreprocessResult - the run should fail outright):
    zero positive examples at all, or too few normal rows to trust even an
    anomaly baseline.
    """
    base_notes = list(notes or [])
    base_context = dict(context or {})

    y = pd.to_numeric(df[target_col], errors="coerce").fillna(0).astype(int)
    positive = int((y == 1).sum())
    normal = int((y == 0).sum())

    if positive == 0:
        raise ValueError(
            f"Zero positive examples in '{target_col}' - nothing to learn from, not even enough to "
            "define what 'normal' looks like relative to an event."
        )

    if positive >= min_positive_examples:
        route_notes = [f"{positive} positive example(s) across {len(y)} rows - training a supervised classifier."]
        if normal > 0:
            ratio = normal / positive
            if ratio > _IMBALANCE_METRIC_SWITCH_RATIO:
                route_notes.append(
                    f"Severe class imbalance ({ratio:.1f}:1 majority:minority) - f1_weighted can still be "
                    "dominated by majority-class performance at this ratio; an imbalance-aware metric "
                    "(minority-class F1 / average precision) would be more appropriate, but "
                    "automl_engine.train_automl_baseline() has no primary-metric override for classification "
                    "(scoring is hardcoded to f1_weighted) - noting this rather than modifying automl_engine.py. "
                    "class_weight='balanced' (the engine's default handle_class_imbalance=True) still applies."
                )
        return PreprocessResult(
            skip_ml=False,
            df=df,
            target_col=target_col,
            task="classification",
            feature_cols=feature_cols,
            notes=base_notes + route_notes,
            context={**base_context, "rare_event_routing": "classification", "target_col": target_col},
        )

    if normal < min_normal_rows:
        raise ValueError(
            f"Only {positive} positive example(s) (< {min_positive_examples} required for a classifier) and "
            f"only {normal} normal row(s) (< {min_normal_rows} required) to fit an anomaly-detection baseline "
            "either - not enough data to trust any model here."
        )

    fit_cols = [c for c in feature_cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    if not fit_cols:
        raise ValueError("No numeric feature columns available to fit an anomaly-detection baseline.")

    normal_df = df.loc[y == 0, fit_cols].apply(pd.to_numeric, errors="coerce")
    normal_df = normal_df.fillna(normal_df.median())
    x_normal = normal_df.to_numpy(dtype=float)
    model = IsolationForest(n_estimators=100, contamination=0.1, random_state=random_state).fit(x_normal)
    mean = x_normal.mean(axis=0)

    all_x = df[fit_cols].apply(pd.to_numeric, errors="coerce")
    all_x = all_x.fillna(pd.Series(mean, index=fit_cols)).to_numpy(dtype=float)
    decision = model.decision_function(all_x)
    # IsolationForest.decision_function is positive for inliers, negative for
    # outliers, unbounded - squashed into a 0-1 "how anomalous" score, same
    # trick predictive_maintenance.py's _anomaly_score uses for its
    # failure_probability scale.
    scores = 1.0 / (1.0 + np.exp(5.0 * decision))
    scores = np.clip(scores, 0.01, 0.99)
    mean_score = round(float(np.mean(scores)), 4)

    route_note = (
        f"Only {positive} positive example(s) (< {min_positive_examples} required) - routed to unsupervised "
        f"anomaly detection (Isolation Forest) fit on {normal} normal row(s) instead of a supervised classifier."
    )

    leaderboard = [{
        "rank": 1,
        "model_name": "IsolationForest",
        "label": "Isolation Forest (anomaly)",
        "is_winner": True,
        "selection_score": mean_score,
        "primary_metric": "anomaly_score",
        "holdout_scores": {"mean_anomaly_score": mean_score},
        "train_seconds": 0.0,
        "params": {"n_estimators": 100, "contamination": 0.1},
        "diagnostics": {},
        "full_data_fit": True,
    }]
    meta = {
        "task": "anomaly",
        "target_column": target_col,
        "model_name": "IsolationForest",
        "metric_name": "anomaly_score",
        "metric_value": mean_score,
        "rows_used": int(len(df)),
        "scores": {"mean_anomaly_score": mean_score},
    }
    engine_result = {
        "pipeline": None,
        "leaderboard": leaderboard,
        "meta": meta,
        "explain_X": None,
        "explain_y": None,
        "notes": [route_note],
    }

    return PreprocessResult(
        skip_ml=True,
        notes=base_notes + [route_note],
        context={
            **base_context,
            "rare_event_routing": "anomaly",
            "engine_result": engine_result,
            "target_col": target_col,
            "anomaly_scores": scores.tolist(),
            "anomaly_fit_cols": fit_cols,
        },
    )


if __name__ == "__main__":
    # ponytail: smallest runnable self-check instead of a full test suite entry point.
    rng = np.random.default_rng(0)
    normal_rows = pd.DataFrame({"x": rng.normal(0, 1, 60), "y": rng.normal(0, 1, 60), "flag": 0})
    few_positive = pd.concat([normal_rows, pd.DataFrame({"x": [8, 9], "y": [8, 9], "flag": 1})], ignore_index=True)
    result = route_rare_event(few_positive, "flag", ["x", "y"], min_positive_examples=20)
    assert result.skip_ml is True and result.context["rare_event_routing"] == "anomaly"

    many_positive = pd.concat(
        [normal_rows, pd.DataFrame({"x": rng.normal(5, 1, 25), "y": rng.normal(5, 1, 25), "flag": 1})],
        ignore_index=True,
    )
    result2 = route_rare_event(many_positive, "flag", ["x", "y"], min_positive_examples=20)
    assert result2.skip_ml is False and result2.context["rare_event_routing"] == "classification"
    print("automl_rare_event self-check OK")
