"""Data drift check (PSI per feature) between a training run's saved explain
sample and a new dataset. Split out of automl_engine.py."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# =========================================================
# Data drift check
# =========================================================


def _psi_for_column(train_series: pd.Series, new_series: pd.Series, is_numeric: bool, n_bins: int = 10) -> float | None:
    """Population Stability Index between a training-time column and a new
    dataset's version. Conventional reading: <0.1 no meaningful shift, 0.1-0.25
    moderate, >0.25 significant drift."""
    train_series = train_series.dropna()
    new_series = new_series.dropna()
    if len(train_series) < 5 or len(new_series) < 5:
        return None

    if is_numeric:
        train_series = pd.to_numeric(train_series, errors="coerce").dropna()
        new_series = pd.to_numeric(new_series, errors="coerce").dropna()
        if len(train_series) < 5 or len(new_series) < 5:
            return None
        edges = np.unique(np.quantile(train_series, np.linspace(0, 1, n_bins + 1)))
        if len(edges) < 3:
            return None
        edges[0], edges[-1] = -np.inf, np.inf
        train_counts, _ = np.histogram(train_series, bins=edges)
        new_counts, _ = np.histogram(new_series, bins=edges)
    else:
        cats = sorted(set(train_series.astype(str).unique()) | set(new_series.astype(str).unique()))
        train_counts = train_series.astype(str).value_counts().reindex(cats, fill_value=0).to_numpy()
        new_counts = new_series.astype(str).value_counts().reindex(cats, fill_value=0).to_numpy()

    train_pct = np.clip(train_counts / max(1, train_counts.sum()), 1e-4, None)
    new_pct = np.clip(new_counts / max(1, new_counts.sum()), 1e-4, None)
    psi = float(np.sum((new_pct - train_pct) * np.log(new_pct / train_pct)))
    return psi if np.isfinite(psi) else None


def _drift_severity(psi: float) -> str:
    if psi < 0.1:
        return "low"
    if psi < 0.25:
        return "moderate"
    return "high"


def check_data_drift(meta: dict[str, Any], explain_X: pd.DataFrame | None, df_new: pd.DataFrame) -> dict[str, Any]:
    """Compares a new dataset's raw feature distributions against the training
    run's saved explain sample via PSI per column."""
    if explain_X is None or len(explain_X) < 5:
        raise ValueError("Explain sample unavailable for this run; retrain to generate one.")

    feat_cols = list((meta or {}).get("raw_feature_columns") or (meta or {}).get("feature_columns") or [])
    numeric_set = set(((meta or {}).get("schema") or {}).get("numeric_features") or [])

    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    for c in feat_cols:
        if c not in df_new.columns or c not in explain_X.columns:
            skipped.append(c)
            continue
        is_numeric = c in numeric_set
        psi = _psi_for_column(explain_X[c], df_new[c], is_numeric)
        if psi is None:
            skipped.append(c)
            continue
        results.append({"feature": c, "psi": round(psi, 4), "severity": _drift_severity(psi), "type": "numeric" if is_numeric else "categorical"})

    results.sort(key=lambda r: r["psi"], reverse=True)
    severities = [r["severity"] for r in results]
    overall = "high" if "high" in severities else "moderate" if "moderate" in severities else "low"

    return {
        "overall_severity": overall,
        "features": results,
        "skipped_features": skipped,
        "rows_compared": int(len(df_new)),
        "train_sample_rows": int(len(explain_X)),
    }
