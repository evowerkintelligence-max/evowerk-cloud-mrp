"""Plain-English explanation + recommendation layer for guided AutoML
template runs. Sits after automl_engine.train_automl_baseline() /
template.postprocess() in automl_runner.py: turns the engine's existing
feature-importance output (via automl_engine.explain_model - SHAP with a
permutation-importance fallback, already built) into a one-sentence
headline plus a canned, domain-specific recommendation.

Never raises. Every step degrades to a simpler fallback; if nothing usable
comes out, explain() returns {"explanation_sentence": None,
"recommendation": None} rather than failing the run.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import automl_engine
from app.services.automl_templates import AutoMLTemplate

# Per-template outcome/horizon vocabulary for the headline sentence. Not
# worth a new AutoMLTemplate field for three lines of copy - a local lookup
# here, with a generic fallback for any template not listed (e.g. tests).
_OUTCOME_LABELS = {
    "scrap_prediction": "scrap",
    "downtime_risk": "downtime duration",
    "predictive_maintenance": "machine failure",
}
_HORIZON_LABELS = {
    "scrap_prediction": "this run",
    "downtime_risk": "this shift",
    "predictive_maintenance": "the next few days",
}

_TREND_HINTS = ("slope", "trend")


def explain(
    engine_result: dict[str, Any] | None,
    template: AutoMLTemplate,
    role_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """engine_result is the raw dict from automl_engine.train_automl_baseline
    (pipeline/leaderboard/meta/explain_X/explain_y) - None when the template
    took a skip_ml (statistical fallback) path, in which case there's no
    model to explain. role_labels maps raw column name -> friendly label;
    missing entries fall back to a title-cased version of the column name."""
    try:
        return _explain(engine_result, template, role_labels or {})
    except Exception:  # noqa: BLE001 - this layer must never fail the run
        return {"explanation_sentence": None, "recommendation": None}


def _friendly(col: str, role_labels: dict[str, str]) -> str:
    label = role_labels.get(col)
    if label:
        return label
    return col.replace("_", " ").replace("-", " ").strip().title()


def _outcome_scores(pipe: Any, task: str, explain_X: pd.DataFrame, explain_y: pd.Series | None) -> np.ndarray | None:
    """A per-row numeric score to correlate driver values against - predicted
    probability of the positive class for binary classification, else the
    actual holdout target. Cheap (reuses the already-fitted pipeline's
    .predict_proba), not a SHAP recomputation."""
    if task == "classification" and pipe is not None and hasattr(pipe, "predict_proba"):
        try:
            classes = list(getattr(pipe, "classes_", []))
            if len(classes) == 2:
                return np.asarray(pipe.predict_proba(explain_X)[:, 1], dtype=float)
        except Exception:
            pass
    if explain_y is not None:
        try:
            return pd.to_numeric(explain_y, errors="coerce").to_numpy(dtype=float)
        except Exception:
            return None
    return None


def _direction(feature: str, explain_X: pd.DataFrame, scores: np.ndarray | None) -> str:
    """"high"/"low" for a driver whose level correlates with the outcome,
    "rising"/"falling" for slope/trend-named drivers, defaulting to "high"
    (an "elevated importance" reading) when direction can't be computed -
    e.g. a categorical driver like machine/reason/shift, where "high/low"
    doesn't apply but the driver still matters."""
    col = explain_X[feature]
    trendy = any(h in feature.lower() for h in _TREND_HINTS)
    if pd.api.types.is_numeric_dtype(col) and scores is not None and len(scores) == len(col):
        try:
            corr = np.corrcoef(col.astype(float).to_numpy(), scores)[0, 1]
        except Exception:
            corr = float("nan")
        if not np.isnan(corr):
            if trendy:
                return "rising" if corr > 0 else "falling"
            return "high" if corr > 0 else "low"
    return "high"


def _tail(lead: str, risk_pct: int | None, outcome: str, horizon: str) -> str:
    if risk_pct is not None:
        return f"{lead} -> {risk_pct}% risk of {outcome} within {horizon}."
    return f"{lead} -> higher predicted {outcome}."


def _recommend(top_driver: tuple[str, str, str] | None, template: AutoMLTemplate) -> str | None:
    if top_driver is None:
        return None
    feature, label, direction = top_driver
    needles = (feature.lower(), label.lower())
    for rule in template.recommendation_rules:
        pattern = rule.driver_pattern.lower()
        if rule.direction == direction and any(pattern in n for n in needles):
            return rule.action_text
    return f"Investigate {label} - it's the strongest predictor."


def _explain(
    engine_result: dict[str, Any] | None, template: AutoMLTemplate, role_labels: dict[str, str],
) -> dict[str, Any]:
    if not engine_result:
        return {"explanation_sentence": None, "recommendation": None}

    meta = engine_result.get("meta") or {}
    pipe = engine_result.get("pipeline")
    explain_X = engine_result.get("explain_X")
    explain_y = engine_result.get("explain_y")
    task = str(meta.get("task") or "").strip().lower()

    top_features: list[dict[str, Any]] = []
    if pipe is not None and explain_X is not None:
        try:
            result = automl_engine.explain_model(pipe, meta, explain_X, explain_y, top_k=4)
            top_features = [f for f in result.get("top_features", []) if f.get("importance")]
        except Exception:
            top_features = []

    scores = _outcome_scores(pipe, task, explain_X, explain_y) if (top_features and explain_X is not None) else None

    drivers: list[tuple[str, str, str]] = []
    for f in top_features:
        feature = f.get("feature")
        if not feature or explain_X is None or feature not in explain_X.columns:
            continue
        label = _friendly(feature, role_labels)
        direction = _direction(feature, explain_X, scores)
        drivers.append((feature, label, direction))
        if len(drivers) == 2:
            break

    outcome = _OUTCOME_LABELS.get(template.id) or _friendly(str(meta.get("target_column") or "the outcome"), role_labels).lower()
    horizon = _HORIZON_LABELS.get(template.id, "the near term")

    risk_pct = None
    if task == "classification" and scores is not None and len(scores):
        risk_pct = int(round(float(np.mean(scores)) * 100))

    if len(drivers) >= 2:
        (_, l1, d1), (_, l2, d2) = drivers[0], drivers[1]
        sentence = _tail(f"{l1} {d1} + {l2} {d2}", risk_pct, outcome, horizon)
    elif len(drivers) == 1:
        _, l1, d1 = drivers[0]
        sentence = _tail(f"{l1} {d1}", risk_pct, outcome, horizon)
    else:
        feat_cols = [c for c in (meta.get("feature_columns") or [])][:3]
        if not feat_cols:
            return {"explanation_sentence": None, "recommendation": None}
        sentence = "Top factors: " + ", ".join(_friendly(c, role_labels) for c in feat_cols) + "."

    recommendation = _recommend(drivers[0] if drivers else None, template)
    return {"explanation_sentence": sentence, "recommendation": recommendation}
