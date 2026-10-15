"""Explainability for fitted automl_engine pipelines: SHAP (preferred) with a
permutation-importance fallback, partial dependence, and single-row
"why this prediction" breakdowns. Split out of automl_engine.py."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

try:
    import shap

    _HAS_SHAP = True
except ImportError:
    shap = None
    _HAS_SHAP = False


# =========================================================
# Explainability: SHAP (preferred) + permutation importance fallback,
# partial dependence
# =========================================================


def _encode_for_shap(Xs: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[Any]]]:
    """SHAP's default tabular masker calls np.isclose() on the raw feature
    matrix, which blows up on string categoricals - so categorical columns are
    mapped to integer codes here (decoded back to real values inside the
    wrapped predict function) purely so the masker only ever sees numbers."""
    encoded = Xs.copy()
    cat_maps: dict[str, list[Any]] = {}
    for c in Xs.columns:
        if not pd.api.types.is_numeric_dtype(Xs[c]):
            cats = sorted(Xs[c].astype(str).unique().tolist())
            cat_maps[c] = cats
            code_map = {v: float(i) for i, v in enumerate(cats)}
            encoded[c] = Xs[c].astype(str).map(code_map).fillna(-1.0).astype(float)
    return encoded, cat_maps


def _apply_shap_encoding(df: pd.DataFrame, cat_maps: dict[str, list[Any]]) -> pd.DataFrame:
    """Encode a frame with a cat_maps already fitted on the background sample,
    so it lines up with the explainer's numeric-only masker."""
    encoded = df.copy()
    for c in df.columns:
        if c in cat_maps:
            code_map = {v: float(i) for i, v in enumerate(cat_maps[c])}
            encoded[c] = df[c].astype(str).map(code_map).fillna(-1.0).astype(float)
        else:
            encoded[c] = pd.to_numeric(df[c], errors="coerce")
    return encoded


def _make_shap_predict_fn(pipe: Any, task: str, columns: list[str], cat_maps: dict[str, list[Any]]) -> Any:
    def _predict(X: Any) -> Any:
        arr = np.atleast_2d(np.asarray(X, dtype=float))
        df = pd.DataFrame(arr, columns=columns)
        for c, cats in cat_maps.items():
            def _decode(v: float, _cats: list[Any] = cats) -> Any:
                idx = int(round(v)) if not pd.isna(v) else 0
                idx = max(0, min(idx, len(_cats) - 1))
                return _cats[idx]
            df[c] = df[c].map(_decode)
        if task == "classification" and hasattr(pipe, "predict_proba"):
            classes_attr = getattr(pipe, "classes_", None)
            classes = list(classes_attr) if classes_attr is not None else []
            proba = pipe.predict_proba(df)
            return proba[:, 1] if len(classes) == 2 else proba
        return pipe.predict(df)
    return _predict


def _build_shap_explainer(pipe: Any, task: str, background: pd.DataFrame) -> tuple[Any, dict[str, list[Any]]]:
    encoded_bg, cat_maps = _encode_for_shap(background)
    # Cap background rows - the model-agnostic explainer's cost scales with
    # background size x features; a small representative sample keeps this responsive.
    bg = encoded_bg.sample(n=50, random_state=42) if len(encoded_bg) > 50 else encoded_bg
    fn = _make_shap_predict_fn(pipe, task, list(background.columns), cat_maps)
    explainer = shap.Explainer(fn, bg)
    return explainer, cat_maps


def _shap_top_features(pipe: Any, task: str, Xs: pd.DataFrame, top_k: int, max_rows: int = 100) -> list[tuple[str, float]]:
    sample = Xs.sample(n=max_rows, random_state=42) if len(Xs) > max_rows else Xs
    explainer, cat_maps = _build_shap_explainer(pipe, task, Xs)
    sv = explainer(_apply_shap_encoding(sample, cat_maps))
    values = np.asarray(sv.values)
    # Multiclass predict_proba -> values shape (rows, features, classes); collapse
    # to a single importance axis by averaging |SHAP| across classes.
    if values.ndim == 3:
        values = np.abs(values).mean(axis=2)
    mean_abs = np.abs(values).mean(axis=0)
    pairs = sorted(zip(list(sample.columns), mean_abs.tolist()), key=lambda t: t[1], reverse=True)
    return pairs[: max(0, int(top_k))]


def explain_model(
    pipe: Any, meta: dict[str, Any], explain_X: pd.DataFrame | None, explain_y: pd.Series | None,
    top_k: int = 12, n_repeats: int = 8, random_state: int = 42,
) -> dict[str, Any]:
    model_name = str((meta or {}).get("model_name", "model"))
    metric_name = str((meta or {}).get("metric_name", "metric"))
    metric_value = float((meta or {}).get("metric_value", 0.0))
    task = str((meta or {}).get("task") or "").strip().lower()
    feat_cols = list((meta or {}).get("feature_columns") or [])

    if explain_X is None or explain_y is None or len(explain_X) < 5:
        top_features = [{"feature": f, "importance": None} for f in feat_cols[: max(0, int(top_k))]]
        narrative = (
            f"Model '{model_name}' trained via AutoML baseline. Primary metric: {metric_name}={metric_value:.4f}. "
            "Feature importances are unavailable because the saved explain sample is missing. Re-train the model "
            "to generate a fresh explain sample."
        )
        return {"top_features": top_features, "narrative": narrative}

    Xs = explain_X
    if feat_cols:
        keep = [c for c in feat_cols if c in Xs.columns]
        if keep:
            Xs = Xs[keep].copy()

    if _HAS_SHAP:
        try:
            pairs = _shap_top_features(pipe, task, Xs, top_k)
            top_features = [{"feature": f, "importance": float(v)} for f, v in pairs]
            narrative = (
                f"Model '{model_name}' trained via AutoML baseline. Primary metric: {metric_name}={metric_value:.4f}. "
                f"Importances shown are SHAP mean |value| computed on a sample of the saved validation set. "
                "Higher means the feature moved the model's prediction more, on average."
            )
            return {"top_features": top_features, "narrative": narrative}
        except Exception:
            pass  # fall through to permutation importance below

    scoring = "accuracy" if task == "classification" else "r2"
    try:
        pi = permutation_importance(pipe, Xs, explain_y, n_repeats=int(n_repeats), random_state=int(random_state), scoring=scoring)
        pairs = list(zip(list(Xs.columns), pi.importances_mean))
        pairs.sort(key=lambda t: (t[1] if t[1] is not None else -1e18), reverse=True)
        top_features = [{"feature": f, "importance": float(v)} for f, v in pairs[: max(0, int(top_k))]]
        narrative = (
            f"Model '{model_name}' trained via AutoML baseline. Primary metric: {metric_name}={metric_value:.4f}. "
            f"Importances shown are permutation importances computed on a saved validation sample "
            f"(scoring='{scoring}', repeats={int(n_repeats)}). Higher importance means the feature contributed "
            "more to predictive performance on the sample."
        )
        return {"top_features": top_features, "narrative": narrative}
    except Exception:
        top_features = [{"feature": f, "importance": None} for f in list(Xs.columns)[: max(0, int(top_k))]]
        narrative = (
            f"Model '{model_name}' trained via AutoML baseline. Primary metric: {metric_name}={metric_value:.4f}. "
            "Could not compute permutation importances (environment/estimator constraints). Features listed are "
            "those used during training."
        )
        return {"top_features": top_features, "narrative": narrative}


def _predict_fn(pipe: Any, task: str) -> Any:
    """Callable(X_df) -> np.ndarray for the partial-dependence sweep. Classification
    uses predict_proba (positive class for binary, full matrix for multiclass) so
    values reflect probability shifts rather than label flips."""
    if task == "classification" and hasattr(pipe, "predict_proba"):
        classes_attr = getattr(pipe, "classes_", None)
        classes = list(classes_attr) if classes_attr is not None else []
        if len(classes) == 2:
            return lambda X: pipe.predict_proba(X)[:, 1]
        return lambda X: pipe.predict_proba(X)
    return lambda X: pipe.predict(X)


def partial_dependence_data(
    pipe: Any, meta: dict[str, Any], explain_X: pd.DataFrame | None, feature: str, grid_size: int = 20
) -> dict[str, Any]:
    task = str((meta or {}).get("task") or "").strip().lower()
    feat_cols = list((meta or {}).get("feature_columns") or [])
    if explain_X is None or len(explain_X) < 5:
        raise ValueError("Explain sample unavailable for this run; retrain to generate one.")
    Xs = explain_X
    if feat_cols:
        keep = [c for c in feat_cols if c in Xs.columns]
        if keep:
            Xs = Xs[keep].copy()
    if not feature:
        raise ValueError("feature required")
    if feature not in Xs.columns:
        raise ValueError(f"Unknown feature '{feature}' for this run.")

    is_numeric = bool(pd.api.types.is_numeric_dtype(Xs[feature]))
    sample = Xs.sample(n=300, random_state=42) if len(Xs) > 300 else Xs

    if is_numeric:
        lo, hi = float(sample[feature].quantile(0.02)), float(sample[feature].quantile(0.98))
        if lo == hi:
            lo, hi = float(sample[feature].min()), float(sample[feature].max())
        grid: list[Any] = np.linspace(lo, hi, max(2, int(grid_size))).tolist()
    else:
        grid = sample[feature].astype(str).value_counts().head(int(grid_size)).index.tolist()

    fn = _predict_fn(pipe, task)
    avg_response: list[float] = []
    for g in grid:
        probe = sample.copy()
        probe[feature] = g
        preds = np.asarray(fn(probe))
        if preds.ndim == 2:
            preds = preds.max(axis=1)
        avg_response.append(float(np.mean(preds)))

    return {
        "feature": feature,
        "is_numeric": is_numeric,
        "grid": [round(g, 6) if isinstance(g, float) else g for g in grid],
        "response": avg_response,
        "rows_used": int(len(sample)),
    }


def _confidence_for_prediction(
    pipe: Any, task: str, Xs: pd.DataFrame, ys: pd.Series | None, prediction: float | None, proba: dict[str, float] | None,
) -> dict[str, Any] | None:
    """Regression: an empirical prediction interval built from the held-out
    explain sample's residuals (actual - predicted), added to this row's point
    prediction - a naive but model-agnostic interval, not a formal statistical
    guarantee, so it's labeled with its method. Classification: predict_proba
    already IS the confidence; this just adds a plain-language label based on
    the margin between the top two classes."""
    if task == "regression" and ys is not None and prediction is not None and len(Xs) >= 10:
        try:
            residuals = ys.to_numpy(dtype=float) - np.asarray(pipe.predict(Xs), dtype=float)
            lo_q, hi_q = np.quantile(residuals, [0.05, 0.95])
            return {
                "lower": float(prediction + lo_q),
                "upper": float(prediction + hi_q),
                "level": 0.90,
                "method": "empirical residual quantiles (5th-95th percentile) from the held-out explain sample",
            }
        except Exception:
            return None
    if task == "classification" and proba:
        sorted_p = sorted(proba.values(), reverse=True)
        margin = sorted_p[0] - (sorted_p[1] if len(sorted_p) > 1 else 0.0)
        label = "high" if margin > 0.5 else "medium" if margin > 0.2 else "low"
        return {"confidence_label": label, "margin": float(margin)}
    return None


def explain_row(
    pipe: Any, meta: dict[str, Any], explain_X: pd.DataFrame | None, explain_y: pd.Series | None,
    row: dict[str, Any], top_k: int = 10,
) -> dict[str, Any]:
    """Single-row prediction + SHAP per-feature contributions ('why this
    prediction'). Unlike explain_model's global importances, this has no
    non-SHAP fallback - a per-row decomposition needs SHAP's additive
    attribution, permutation importance only answers the global question."""
    if not _HAS_SHAP:
        raise RuntimeError("SHAP is not installed on this machine.")
    task = str((meta or {}).get("task") or "").strip().lower()
    if task not in ("classification", "regression"):
        raise ValueError(f"Per-row explanation is not available for task '{task}'.")
    feat_cols = list((meta or {}).get("feature_columns") or [])
    if explain_X is None or len(explain_X) < 5:
        raise ValueError("Explain sample unavailable for this run; retrain to generate one.")

    Xs = explain_X
    if feat_cols:
        keep = [c for c in feat_cols if c in Xs.columns]
        if keep:
            Xs = Xs[keep].copy()
    cols = list(Xs.columns)

    row_df = pd.DataFrame([row])
    for c in cols:
        if c not in row_df.columns:
            row_df[c] = np.nan
    row_df = row_df[cols]
    for c in cols:
        if pd.api.types.is_numeric_dtype(Xs[c]):
            row_df[c] = pd.to_numeric(row_df[c], errors="coerce")

    prediction = pipe.predict(row_df)[0]
    proba = None
    classes: list[Any] = []
    if task == "classification" and hasattr(pipe, "predict_proba"):
        try:
            p = pipe.predict_proba(row_df)[0]
            classes_attr = getattr(pipe, "classes_", None)
            classes = list(classes_attr) if classes_attr is not None else []
            proba = {str(c): float(v) for c, v in zip(classes, p)}
        except Exception:
            proba = None

    confidence = _confidence_for_prediction(pipe, task, Xs, explain_y, float(prediction) if task == "regression" else None, proba)

    explainer, cat_maps = _build_shap_explainer(pipe, task, Xs)
    sv = explainer(_apply_shap_encoding(row_df, cat_maps))

    values = np.asarray(sv.values[0])
    base_value: Any = sv.base_values[0]
    if values.ndim == 2:  # multiclass: (features, classes) - explain the predicted class
        try:
            class_idx = classes.index(prediction) if classes else 0
        except ValueError:
            class_idx = 0
        base_value = float(np.asarray(base_value)[class_idx])
        values = values[:, class_idx]
    else:
        base_value = float(base_value)

    def _native(v: Any) -> Any:
        return v.item() if hasattr(v, "item") else v

    contributions = sorted(
        [{"feature": c, "value": _native(row_df.iloc[0][c]), "contribution": float(v)} for c, v in zip(cols, values.tolist())],
        key=lambda d: abs(d["contribution"]), reverse=True,
    )[: max(0, int(top_k))]

    return {
        "prediction": prediction.item() if hasattr(prediction, "item") else prediction,
        "probabilities": proba,
        "base_value": base_value,
        "contributions": contributions,
        "confidence": confidence,
    }
