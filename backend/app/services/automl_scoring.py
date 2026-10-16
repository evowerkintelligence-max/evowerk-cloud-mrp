"""Scoring helpers (per-task metrics, holdout diagnostics, param cleanup,
primary-metric selection) for automl_engine. Split out of automl_engine.py;
pure functions over in-memory pandas objects."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
)

from app.services.automl_models import AutoMLTask


# =========================================================
# Scoring + diagnostics
# =========================================================


def _scoring_for_task(task: AutoMLTask) -> str:
    if task == AutoMLTask.classification:
        return "f1_weighted"
    if task == AutoMLTask.regression:
        return "neg_root_mean_squared_error"
    return "neg_mean_absolute_error"  # forecasting


def _score_classification(y_true, y_pred) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1": float(f1_score(y_true, y_pred, average="weighted")),
    }


def _score_regression(y_true, y_pred) -> dict[str, float]:
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    return {"rmse": rmse, "mae": float(mean_absolute_error(y_true, y_pred)), "r2": float(r2_score(y_true, y_pred))}


def _regression_diagnostics(y_true, y_pred, max_points: int = 500) -> dict[str, Any]:
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    n = len(yt)
    if n > max_points:
        idx = np.linspace(0, n - 1, max_points).astype(int)
        yt, yp = yt[idx], yp[idx]
    resid = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return {
        "type": "regression",
        "points": [[round(float(a), 4), round(float(b), 4)] for a, b in zip(yt, yp)],
        "residual_std": float(np.std(resid)) if n else 0.0,
    }


def _classification_diagnostics(y_true, y_pred, max_labels: int = 20) -> dict[str, Any]:
    labels = sorted({str(v) for v in list(y_true) + list(y_pred)})
    truncated = len(labels) > max_labels
    labels = labels[:max_labels]
    y_true_s = [str(v) for v in y_true]
    y_pred_s = [str(v) for v in y_pred]
    cm = confusion_matrix(y_true_s, y_pred_s, labels=labels).astype(int).tolist()
    prec, rec, _, support = precision_recall_fscore_support(
        y_true_s, y_pred_s, labels=labels, average=None, zero_division=0
    )
    per_class = [
        {"label": labels[i], "precision": float(prec[i]), "recall": float(rec[i]), "support": int(support[i])}
        for i in range(len(labels))
    ]
    return {"type": "classification", "labels": labels, "confusion": cm, "per_class": per_class, "truncated": truncated}


def _make_diagnostics(task: AutoMLTask, y_true, y_pred) -> dict[str, Any]:
    try:
        if task == AutoMLTask.classification:
            return _classification_diagnostics(y_true, y_pred)
        return _regression_diagnostics(y_true, y_pred)
    except Exception:
        return {}


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Strip the pipeline 'model__' prefix and JSON-safe the tuned hyperparameters."""
    import json

    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        key = k.split("__", 1)[1] if "__" in k else k
        try:
            json.dumps(v)
            out[key] = v
        except Exception:
            out[key] = str(v)
    return out


def _pick_primary_metric(task: AutoMLTask, scores: dict[str, float]) -> tuple[str, float]:
    if task == AutoMLTask.classification:
        return "f1", float(scores.get("f1", 0.0))
    if task == AutoMLTask.regression:
        return "rmse", float(scores.get("rmse", 1e18))
    return "mae", float(scores.get("mae", 1e18))  # forecasting


def normalize_higher_is_better(task: AutoMLTask, metric_value: float) -> float:
    """Flips lower-is-better metrics (rmse/mae) so every selection score compares as 'higher wins'."""
    return metric_value if task == AutoMLTask.classification else -metric_value
