"""AutoML training engine: sklearn model bake-off + hyperparameter search +
explainability, ported from the NovaEdgeElectron desktop app's
backend/automl_core.py.

Deliberately trimmed to a core MVP scope (see the port plan): tasks are
classification/regression/forecasting only (no clustering/anomaly), no auto
feature-engineering. Fast-follow if needed. XGBoost/LightGBM (boosted-tree
candidates), Optuna (Bayesian hyperparameter search, search_method="bayesian"),
and SHAP (preferred explainability method in explain_model, falls back to
permutation importance) are all optional at runtime - each degrades
gracefully with a note if its library isn't installed.

Every function here is a pure function over in-memory pandas objects — no
org_id, no file paths, no dataset/DB access. app/services/automl_runner.py
owns loading a Dataset into a DataFrame and persisting the fitted pipeline /
explain sample via app/services/automl_storage.py.

Split across siblings (re-exported here, so callers keep importing from
automl_engine): automl_preprocessing (feature screening + ColumnTransformer),
automl_models (task enum, model roster, search spaces, CV planning),
automl_explain (SHAP / permutation importance / partial dependence / per-row),
automl_drift (PSI data drift check).
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import StackingClassifier, StackingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
)
from sklearn.model_selection import RandomizedSearchCV, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline

from app.services.automl_drift import check_data_drift  # noqa: F401
from app.services.automl_explain import (  # noqa: F401
    explain_model,
    explain_row,
    partial_dependence_data,
)
from app.services.automl_models import (  # noqa: F401
    _HAS_OPTUNA,
    _SEARCH_N_JOBS,
    AutoMLTask,
    _build_candidates,
    _cv_plan,
    _param_distributions,
    _pretty_model_name,
    _run_optuna_search,
    _selection_subsample,
    _thoroughness_profile,
    algorithm_roster,
)
from app.services.automl_preprocessing import (
    _make_preprocessor,
    _screen_features,
    _split_numeric_categorical,
)

# Categorical columns with more distinct values than this route through target
# encoding instead of one-hot, to avoid dimensionality blowup on high-cardinality
# columns (machine id, operator, SKU, ...).
_HIGH_CARDINALITY_THRESHOLD = 20

# Forecasting lag/rolling feature windows (of the target's own history).
_LAGS = (1, 2, 3, 7, 14)
_ROLLING_WINDOWS = (7, 14)
N_LAGS = max(_LAGS)



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


# =========================================================
# Forecasting: lag/rolling/calendar features of the target's own history
# =========================================================


def _add_target_lag_features(df: pd.DataFrame, time_col: str, target_col: str) -> tuple[pd.DataFrame, list[str]]:
    """Assumes df is already sorted by time_col. Returns (df_with_features, new_column_names)."""
    out = df.copy()
    new_cols: list[str] = []
    # CSV/TSV-sourced datasets always load columns as strings (see
    # app.services.datasets.dataframe_for) - coerce before any lag/rolling/
    # trend arithmetic below, which raises TypeError on object-dtype values.
    s = pd.to_numeric(out[target_col], errors="coerce")

    for lag in _LAGS:
        col = f"_lag_{lag}_"
        out[col] = s.shift(lag)
        new_cols.append(col)

    for w in _ROLLING_WINDOWS:
        mean_col, std_col = f"_roll_mean_{w}_", f"_roll_std_{w}_"
        out[mean_col] = s.shift(1).rolling(window=w, min_periods=w).mean()
        out[std_col] = s.shift(1).rolling(window=w, min_periods=w).std()
        new_cols.extend([mean_col, std_col])

    out["_trend_7_"] = (s.shift(1) - s.shift(7)) / 7.0
    new_cols.append("_trend_7_")

    dt = pd.to_datetime(out[time_col], errors="coerce")
    out["_dow_"] = dt.dt.dayofweek.astype(float)
    out["_month_"] = dt.dt.month.astype(float)
    out["_seasonality_"] = np.sin(2 * np.pi * dt.dt.dayofyear.astype(float) / 365.0)
    new_cols.extend(["_dow_", "_month_", "_seasonality_"])

    return out, new_cols


# =========================================================
# Training
# =========================================================


def train_automl_baseline(
    df: pd.DataFrame,
    target_column: str,
    task: AutoMLTask,
    feature_columns: list[str] | None = None,
    test_size: float = 0.2,
    random_state: int = 42,
    time_column: str | None = None,
    horizon: int = 1,
    algorithms: list[str] | None = None,
    thoroughness: str = "balanced",
    handle_class_imbalance: bool = True,
    forecast_strategy: str = "direct",
    time_budget_seconds: int | None = None,
    max_rows: int = 200_000,
    search_method: str = "random",
) -> dict[str, Any]:
    """sklearn baseline bake-off: model selection via k-fold (or TimeSeriesSplit
    for forecasting) cross-validation with a randomized hyperparameter search
    per candidate, evaluated once on an untouched holdout test set. Heuristic
    leakage/data-quality screening on auto-inferred features; high-cardinality
    categoricals routed through target encoding instead of one-hot.

    Returns {"pipeline", "leaderboard", "meta", "explain_X", "explain_y", "notes"}.
    """
    if not target_column:
        raise ValueError("target_column required")
    if target_column not in df.columns:
        raise ValueError(f"Target column not found: {target_column}")

    notes: list[str] = []
    if len(df) > max_rows:
        # ponytail: head-truncate rather than a smarter (e.g. time-aware tail)
        # sample - matches the source app's plain row-cap; revisit if forecasting
        # on oversized datasets needs the most-recent rows instead.
        notes.append(f"Dataset truncated to the first {max_rows:,} of {len(df):,} rows for training.")
        df = df.head(max_rows).copy()
    lag_feature_cols: list[str] = []
    forecast_strategy = str(forecast_strategy or "direct").lower().strip()
    if forecast_strategy not in ("direct", "recursive"):
        forecast_strategy = "direct"
    effective_horizon = 1 if (task == AutoMLTask.forecasting and forecast_strategy == "recursive") else int(horizon)

    if task == AutoMLTask.forecasting:
        if not time_column or time_column not in df.columns:
            raise ValueError("Forecasting requires a valid time_column present in dataset")
        df = df.sort_values(by=time_column).reset_index(drop=True)
        # CSV/TSV-sourced datasets load every column as a string (see
        # app/services/datasets.py's relation_for), so the lag/rolling/trend
        # arithmetic below needs a real numeric dtype before it can run.
        df[target_column] = pd.to_numeric(df[target_column], errors="coerce")
        df, lag_feature_cols = _add_target_lag_features(df, time_column, target_column)
        n_before_warmup = len(df)
        df = df.dropna(subset=lag_feature_cols).reset_index(drop=True)
        dropped_warmup = n_before_warmup - len(df)
        if dropped_warmup > 0:
            notes.append(
                f"Forecasting: dropped {dropped_warmup} earliest row(s) with insufficient history to "
                "compute lag/rolling features."
            )
        df["_target_future_"] = df[target_column].shift(-int(effective_horizon))
        if int(effective_horizon) > 0:
            df = df.iloc[: -int(effective_horizon)].copy()
        target_col = "_target_future_"
        if forecast_strategy == "recursive":
            notes.append(
                f"Forecasting (recursive): model trained to predict 1 step ahead; at forecast time each "
                f"prediction feeds back into the lag features to walk out a {int(horizon)}-step trajectory."
            )
        else:
            notes.append(
                f"Forecasting: target is '{target_column}' shifted by horizon={horizon}, using "
                f"{len(lag_feature_cols)} engineered lag/rolling/calendar features of its own history."
            )
    else:
        target_col = target_column

    df = df.dropna(subset=[target_col]).copy()
    feat_cols = [c for c in (feature_columns or [c for c in df.columns if c != target_col]) if c in df.columns and c != target_col]
    if not feat_cols:
        raise ValueError("Provided feature_columns are empty / not found in dataset")
    if task == AutoMLTask.forecasting and time_column in feat_cols:
        feat_cols = [c for c in feat_cols if c != time_column]

    # Engineered lag/rolling/calendar columns + the raw target's current value
    # are legitimate forecasting predictors (autocorrelation is signal here,
    # not leakage) — force-included and exempt from the screen below.
    if task == AutoMLTask.forecasting:
        always_include = [c for c in (list(lag_feature_cols) + [target_column]) if c in df.columns]
    else:
        always_include = []
    exempt = set(always_include)

    screenable_cols = [c for c in feat_cols if c not in exempt]
    if feature_columns:
        kept_cols = screenable_cols
    else:
        kept_cols, screen_notes = _screen_features(df, target_col, screenable_cols)
        notes.extend(screen_notes)
    feat_cols = list(kept_cols) + [c for c in always_include if c not in kept_cols]
    if not feat_cols:
        raise ValueError("No usable feature columns remain after screening")

    X = df[feat_cols].copy()
    y = df[target_col].copy()
    if task != AutoMLTask.classification:
        y = pd.to_numeric(y, errors="coerce").astype(float)
    else:
        # CSV-sourced datasets always load columns as strings (see
        # app/services/datasets.py's dataframe_for), so a numeric-looking label
        # column (e.g. "0"/"1") arrives as dtype=object. sklearn's
        # class_weight="balanced" validation for RandomForestClassifier/
        # ExtraTreesClassifier breaks on that (ValueError: "The classes, [0, 1],
        # are not in class_weight") - coerce to a real numeric dtype when every
        # value round-trips cleanly; true string labels (e.g. "east"/"west") are
        # left untouched.
        numeric_y = pd.to_numeric(y, errors="coerce")
        if numeric_y.notna().all():
            y = numeric_y

    if task == AutoMLTask.forecasting:
        n = len(df)
        split = int(n * (1.0 - float(test_size)))
        if split <= 1 or split >= n - 1:
            raise ValueError("Not enough rows for forecasting split with given test_size")
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]
    else:
        use_stratify = task == AutoMLTask.classification and y.value_counts().min() >= 2
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=float(test_size), random_state=int(random_state), shuffle=True,
            stratify=y if use_stratify else None,
        )
        if task == AutoMLTask.classification and not use_stratify:
            notes.append("Stratified split skipped — some classes have too few samples.")

    raw_feat_cols = list(feat_cols)
    num_cols, cat_cols = _split_numeric_categorical(X_train, feat_cols)
    cat_cardinality = {c: int(X_train[c].nunique(dropna=True)) for c in cat_cols}
    cat_cols_low = [c for c in cat_cols if cat_cardinality.get(c, 0) <= _HIGH_CARDINALITY_THRESHOLD]
    cat_cols_high = [c for c in cat_cols if cat_cardinality.get(c, 0) > _HIGH_CARDINALITY_THRESHOLD]
    if cat_cols_high:
        notes.append(
            "High-cardinality categorical column(s) " + ", ".join(f"'{c}'" for c in cat_cols_high)
            + f" encoded via target encoding instead of one-hot (>{_HIGH_CARDINALITY_THRESHOLD} distinct values)."
        )

    te_target_type = "continuous" if task != AutoMLTask.classification else "auto"
    pre = _make_preprocessor(num_cols, cat_cols_low, cat_cols_high, target_type=te_target_type)

    class_distribution: dict[str, int] | None = None
    imbalance_ratio: float | None = None
    imbalance_detected = False
    class_weight_balanced = False
    scale_pos_weight: float | None = None
    if task == AutoMLTask.classification:
        counts = y_train.value_counts()
        class_distribution = {str(k): int(v) for k, v in counts.items()}
        if len(counts) >= 2 and counts.min() > 0:
            imbalance_ratio = round(float(counts.max()) / float(counts.min()), 2)
            imbalance_detected = imbalance_ratio >= 3.0
            if handle_class_imbalance:
                class_weight_balanced = True
                if len(counts) == 2:
                    scale_pos_weight = float(counts.max()) / float(counts.min())
                notes.append(
                    f"Class imbalance handling: majority:minority ratio is {imbalance_ratio}:1 "
                    f"({'flagged as imbalanced, ' if imbalance_detected else ''}class_weight='balanced' applied "
                    "so the minority class isn't drowned out by the loss function)."
                )
            elif imbalance_detected:
                notes.append(
                    f"Class imbalance detected (majority:minority ratio {imbalance_ratio}:1) but handling "
                    "was turned off — minority-class predictions may be unreliable."
                )

    profile = _thoroughness_profile(thoroughness)
    n_train = len(X_train)
    candidates = _build_candidates(
        task, n_train, algorithms, str(profile.get("tier", "standard")), random_state,
        class_weight_balanced=class_weight_balanced, scale_pos_weight=scale_pos_weight,
    )
    if not candidates:
        raise ValueError("No valid algorithms selected to train")
    notes.append(
        f"Thoroughness '{thoroughness}': {len(candidates)} model(s) in the bake-off "
        f"({', '.join(_pretty_model_name(n) for n, _ in candidates)})."
    )

    use_bayesian = str(search_method or "random").lower().strip() == "bayesian"
    if use_bayesian and not _HAS_OPTUNA:
        notes.append("Bayesian search requested but optuna is not installed — falling back to random search.")
        use_bayesian = False

    cv_y = y_train if task == AutoMLTask.classification else None
    splitter, cv_folds, n_iter = _cv_plan(task, n_train, cv_y, random_state, profile)
    if splitter is None:
        notes.append(
            f"Dataset too small for cross-validation (training split has {n_train} rows, need >=40) — "
            "using a single train/test split for model selection."
        )
        selection_method = "single_split"
    else:
        method_label = "Bayesian (Optuna TPE)" if use_bayesian else "randomized"
        notes.append(f"Model selection via {cv_folds}-fold cross-validation with up to {n_iter}-combo {method_label} hyperparameter search per model.")
        selection_method = "cv"

    search_deadline: float | None = None
    if time_budget_seconds:
        search_deadline = time.time() + float(time_budget_seconds)
        notes.append(
            f"Time-budget mode: hyperparameter search capped at {int(time_budget_seconds)}s total across all "
            f"candidates{' (Bayesian trials stop early per-candidate as the budget runs out)' if use_bayesian else ' (remaining candidates are skipped once the budget is exhausted)'}."
        )

    scoring = _scoring_for_task(task)
    X_sel, y_sel, sampled = _selection_subsample(task, X_train, y_train, random_state, int(profile.get("sample_cap", 6000)))
    if sampled:
        notes.append(
            f"Hyperparameter search ran on a {len(X_sel)}-row sample of the {n_train} training rows for "
            f"speed; the winning model is refit on all {n_train} rows."
        )

    def _holdout_scores(fitted_estimator) -> tuple[dict[str, float], Any]:
        y_hat = fitted_estimator.predict(X_test)
        sc = _score_classification(y_test, y_hat) if task == AutoMLTask.classification else _score_regression(y_test, y_hat)
        return sc, y_hat

    def _eval_candidate(name: str, estimator: Any) -> dict[str, Any]:
        pipe = Pipeline(steps=[("pre", pre), ("model", estimator)])
        t0 = time.time()
        params: dict[str, Any] = {}
        if splitter is not None:
            param_dist = _param_distributions(name)
            if use_bayesian and param_dist:
                per_candidate_timeout = max(5.0, search_deadline - time.time()) if search_deadline is not None else None
                selection_score, fitted, params = _run_optuna_search(
                    pipe, name, X_sel, y_sel, splitter, scoring, n_iter, per_candidate_timeout, random_state,
                )
            elif param_dist:
                search = RandomizedSearchCV(
                    pipe, param_distributions=param_dist, n_iter=n_iter, cv=splitter, scoring=scoring,
                    n_jobs=_SEARCH_N_JOBS, random_state=random_state, refit=True, error_score=np.nan,
                )
                search.fit(X_sel, y_sel)
                selection_score = float(search.best_score_)
                fitted = search.best_estimator_
                params = _clean_params(search.best_params_)
            else:
                cv_scores = cross_val_score(pipe, X_sel, y_sel, cv=splitter, scoring=scoring, n_jobs=_SEARCH_N_JOBS, error_score=np.nan)
                selection_score = float(np.nanmean(cv_scores))
                fitted = pipe.fit(X_sel, y_sel)
        else:
            fitted = pipe.fit(X_train, y_train)
            selection_score = float("nan")

        scores, y_hat = _holdout_scores(fitted)
        if splitter is None:
            _, mv = _pick_primary_metric(task, scores)
            selection_score = normalize_higher_is_better(task, mv)
        return {
            "model_name": name,
            "selection_score": selection_score,
            "holdout_scores": scores,
            "train_seconds": round(time.time() - t0, 2),
            "params": params,
            "diagnostics": _make_diagnostics(task, y_test, y_hat),
            "_unfitted": clone(fitted),
            "full_data_fit": not sampled,
        }

    results: list[dict[str, Any]] = []
    skipped_for_time: list[str] = []
    for name, model in candidates:
        # Random search has no native mid-fit timeout, so once the budget is gone
        # remaining candidates are skipped outright. Bayesian search instead
        # time-boxes each candidate's own trial loop above via per_candidate_timeout,
        # so it never hits this branch.
        if search_deadline is not None and not use_bayesian and time.time() >= search_deadline:
            skipped_for_time.append(name)
            continue
        try:
            row = _eval_candidate(name, model)
        except Exception as exc:
            notes.append(f"Candidate '{_pretty_model_name(name)}' failed and was skipped: {exc}")
            continue
        if row["selection_score"] != row["selection_score"]:
            notes.append(f"Candidate '{_pretty_model_name(name)}' produced no valid score and was skipped.")
            continue
        results.append(row)

    if skipped_for_time:
        notes.append(
            "Time budget exhausted before training: "
            + ", ".join(_pretty_model_name(n) for n in skipped_for_time) + "."
        )

    if profile.get("stacking") and splitter is not None and len(results) >= 2:
        try:
            top = sorted(results, key=lambda r: r["selection_score"], reverse=True)[:3]
            base = [(r["model_name"], clone(r["_unfitted"])) for r in top]
            if task == AutoMLTask.classification:
                stack_name = "StackingClassifier"
                stack = StackingClassifier(estimators=base, final_estimator=LogisticRegression(max_iter=1000), cv=3, n_jobs=1)
            else:
                stack_name = "StackingRegressor"
                stack = StackingRegressor(estimators=base, final_estimator=Ridge(), cv=3, n_jobs=1)
            t0 = time.time()
            stack.fit(X_sel, y_sel)
            scores, y_hat = _holdout_scores(stack)
            _, mv = _pick_primary_metric(task, scores)
            results.append({
                "model_name": stack_name,
                "selection_score": normalize_higher_is_better(task, mv),
                "holdout_scores": scores,
                "train_seconds": round(time.time() - t0, 2),
                "params": {"base_models": [_pretty_model_name(r["model_name"]) for r in top]},
                "diagnostics": _make_diagnostics(task, y_test, y_hat),
                "_unfitted": clone(stack),
                "full_data_fit": not sampled,
            })
            notes.append("Added a stacked ensemble of the top models (evaluated on the holdout).")
        except Exception as exc:
            notes.append(f"Stacked ensemble could not be built: {exc}")

    if not results:
        raise RuntimeError("AutoML could not train any candidate model")

    results.sort(key=lambda r: r["selection_score"], reverse=True)
    winner = results[0]
    best_name = winner["model_name"]
    best_pipeline = winner["_unfitted"].fit(X_train, y_train)
    y_pred = best_pipeline.predict(X_test)
    best_scores = _score_classification(y_test, y_pred) if task == AutoMLTask.classification else _score_regression(y_test, y_pred)
    best_metric_name, best_metric_value = _pick_primary_metric(task, best_scores)
    winner["holdout_scores"] = best_scores
    winner["diagnostics"] = _make_diagnostics(task, y_test, y_pred)

    _lower = best_metric_name in ("rmse", "mae")

    def _primary_of(r: dict[str, Any]) -> float:
        v = (r.get("holdout_scores") or {}).get(best_metric_name)
        if v is None:
            return 1e18 if _lower else -1e18
        return float(v)

    results_display = sorted(results, key=_primary_of, reverse=not _lower)
    leaderboard: list[dict[str, Any]] = []
    for rank, r in enumerate(results_display, start=1):
        leaderboard.append({
            "rank": rank,
            "model_name": r["model_name"],
            "label": _pretty_model_name(r["model_name"]),
            "is_winner": r["model_name"] == best_name,
            "selection_score": round(float(r["selection_score"]), 6),
            "primary_metric": best_metric_name,
            "holdout_scores": r["holdout_scores"],
            "train_seconds": r["train_seconds"],
            "params": r["params"],
            "diagnostics": r["diagnostics"],
            "full_data_fit": bool(r["model_name"] == best_name or r.get("full_data_fit", True)),
        })

    categorical_known_values = {c: sorted(str(v) for v in X_train[c].dropna().unique().tolist()) for c in cat_cols_low}
    explain_cap = 2000
    explain_X = X_test.head(explain_cap).copy()
    explain_y = y_test.head(explain_cap).copy()

    meta: dict[str, Any] = {
        "task": task.value,
        "target_column": target_column,
        "effective_target": target_col,
        "feature_columns": feat_cols,
        "raw_feature_columns": raw_feat_cols,
        "time_column": time_column,
        "horizon": int(horizon) if task == AutoMLTask.forecasting else None,
        "forecast_strategy": forecast_strategy if task == AutoMLTask.forecasting else None,
        "effective_horizon": int(effective_horizon) if task == AutoMLTask.forecasting else None,
        "model_name": best_name,
        "metric_name": best_metric_name,
        "metric_value": float(best_metric_value),
        "scores": best_scores,
        "rows_used": int(len(df)),
        "class_balance": {
            "distribution": class_distribution,
            "imbalance_ratio": imbalance_ratio,
            "imbalance_detected": imbalance_detected,
            "handling_applied": class_weight_balanced,
        } if task == AutoMLTask.classification else None,
        "model_selection": {
            "method": selection_method,
            "cv_folds": cv_folds,
            "search_iterations": n_iter,
            "thoroughness": str(thoroughness),
            "search_method": "bayesian" if use_bayesian else "random",
            "time_budget_seconds": int(time_budget_seconds) if time_budget_seconds else None,
            "models_tried": len(leaderboard),
        },
        "schema": {
            "numeric_features": num_cols,
            "categorical_features": cat_cols,
            "categorical_features_high_cardinality": cat_cols_high,
            "categorical_known_values": categorical_known_values,
        },
    }

    return {
        "pipeline": best_pipeline,
        "leaderboard": leaderboard,
        "meta": meta,
        "explain_X": explain_X,
        "explain_y": explain_y,
        "notes": notes,
    }


# =========================================================
# Prediction
# =========================================================


def predict_with_model(pipe: Any, meta: dict[str, Any], df: pd.DataFrame, max_preview_rows: int = 30) -> dict[str, Any]:
    original_columns = list(df.columns)
    target_column = str((meta or {}).get("target_column") or "").strip()
    effective_target = str((meta or {}).get("effective_target") or target_column).strip()
    feat_cols = list((meta or {}).get("feature_columns") or [])
    task = str((meta or {}).get("task") or "").strip().lower()
    time_column = str((meta or {}).get("time_column") or "").strip()

    if task == "forecasting" and time_column and time_column in df.columns and target_column in df.columns:
        df = df.sort_values(by=time_column).reset_index(drop=True)
        df, _ = _add_target_lag_features(df, time_column, target_column)

    if not feat_cols:
        if effective_target and effective_target in df.columns:
            feat_cols = [c for c in df.columns if c != effective_target]
        elif target_column and target_column in df.columns:
            feat_cols = [c for c in df.columns if c != target_column]
        else:
            feat_cols = list(df.columns)

    X = df.copy()
    if effective_target and effective_target in X.columns and effective_target not in feat_cols:
        X = X.drop(columns=[effective_target])
    if target_column and target_column in X.columns and target_column not in feat_cols:
        X = X.drop(columns=[target_column])

    existing = [c for c in feat_cols if c in X.columns]
    if existing:
        X = X[existing].copy()

    notes: list[str] = []
    known_values = ((meta or {}).get("schema") or {}).get("categorical_known_values") or {}
    for col, known in known_values.items():
        if col not in X.columns:
            continue
        unseen = set(str(v) for v in X[col].dropna().unique().tolist()) - set(known or [])
        if unseen:
            sample = ", ".join(sorted(unseen)[:5])
            notes.append(
                f"Column '{col}' has {len(unseen)} unseen categor{'y' if len(unseen) == 1 else 'ies'} not seen "
                f"during training (e.g. {sample}); those rows fall back to a neutral encoding and may be less reliable."
            )

    out_df = df[[c for c in original_columns if c in df.columns]].copy()
    out_df["prediction"] = pipe.predict(X)

    preview_rows = out_df.head(int(max_preview_rows)).to_dict(orient="records")
    return {"preview": preview_rows, "columns": list(out_df.columns), "rows": int(len(out_df)), "notes": notes}


# =========================================================
# Recursive multi-step forecast trajectory
# =========================================================


def forecast_trajectory(
    pipe: Any,
    meta: dict[str, Any],
    df: pd.DataFrame,
    explain_X: pd.DataFrame | None,
    explain_y: pd.Series | None,
    steps: int | None = None,
    history_points: int = 60,
) -> dict[str, Any]:
    """Walk a recursive-strategy forecasting model forward from the end of the
    dataset: predict t+1, append it to the history buffer so the next step's
    lag/rolling features are computed from it, and repeat.

    Uncertainty: reuses the run's empirical residual quantiles (5th/95th
    percentile of holdout residuals) scaled by sqrt(step), the standard
    error-accumulation shape for a recursive walk."""
    task = str((meta or {}).get("task") or "").strip().lower()
    strategy = str((meta or {}).get("forecast_strategy") or "direct").strip().lower()
    if task != "forecasting":
        raise ValueError("Trajectory forecasting is only available for forecasting runs.")
    if strategy != "recursive":
        raise ValueError(
            "This run was trained with the direct (single-point) strategy — re-train it with the recursive "
            "strategy to produce a multi-step trajectory."
        )

    time_column = str((meta or {}).get("time_column") or "").strip()
    target_column = str((meta or {}).get("target_column") or "").strip()
    feat_cols = list((meta or {}).get("feature_columns") or [])
    if not time_column or not target_column or not feat_cols:
        raise ValueError("Run metadata is missing time/target/feature columns; re-train the model.")

    n_steps = max(1, min(int(steps or (meta or {}).get("horizon") or 14), 365))

    if time_column not in df.columns or target_column not in df.columns:
        raise ValueError(f"Dataset is missing '{time_column}' or '{target_column}'.")
    df = df.sort_values(by=time_column).reset_index(drop=True)
    df[target_column] = pd.to_numeric(df[target_column], errors="coerce")
    df = df.dropna(subset=[target_column]).reset_index(drop=True)

    min_history = N_LAGS + 1
    if len(df) < min_history:
        raise ValueError(f"Recursive forecasting needs at least {min_history} rows of history (dataset has {len(df)} usable rows).")

    dt = pd.to_datetime(df[time_column], errors="coerce")
    if dt.isna().all():
        raise ValueError(f"Time column '{time_column}' could not be parsed as dates.")
    diffs = dt.diff().dropna()
    time_step = diffs.median() if len(diffs) else pd.Timedelta(days=1)
    if pd.isna(time_step) or time_step <= pd.Timedelta(0):
        time_step = pd.Timedelta(days=1)
    df[time_column] = dt

    notes: list[str] = []
    lo_q = hi_q = None
    if explain_X is not None and explain_y is not None and len(explain_X) >= 10:
        try:
            keep = [c for c in feat_cols if c in explain_X.columns]
            residuals = explain_y.to_numpy(dtype=float) - np.asarray(pipe.predict(explain_X[keep] if keep else explain_X), dtype=float)
            lo_q, hi_q = (float(v) for v in np.quantile(residuals, [0.05, 0.95]))
        except Exception:
            lo_q = hi_q = None
    if lo_q is None:
        notes.append("Confidence band unavailable — the run's explain sample is missing; re-train to restore it.")

    engineered = {c for c in feat_cols if c.startswith("_") and c.endswith("_")}
    exog_cols = [c for c in feat_cols if c not in engineered and c != target_column and c in df.columns]
    if exog_cols:
        notes.append(
            "Future values of " + ", ".join(f"'{c}'" for c in exog_cols)
            + " are unknown — their last observed values are carried forward for every forecast step."
        )

    buffer_cols = [time_column, target_column] + exog_cols
    buffer = df[buffer_cols].tail(N_LAGS + 6).copy().reset_index(drop=True)
    carried = {c: buffer.iloc[-1][c] for c in exog_cols}

    points: list[dict[str, Any]] = []
    for h in range(1, n_steps + 1):
        feat_df, _ = _add_target_lag_features(buffer, time_column, target_column)
        row_df = feat_df.iloc[[-1]].copy()
        for c in feat_cols:
            if c not in row_df.columns:
                row_df[c] = np.nan
        row_df = row_df[feat_cols]

        pred = float(pipe.predict(row_df)[0])
        next_time = buffer.iloc[-1][time_column] + time_step

        point: dict[str, Any] = {"step": h, "time": next_time.isoformat(), "prediction": round(pred, 4)}
        if lo_q is not None and hi_q is not None:
            scale = float(np.sqrt(h))
            point["lower"] = round(pred + lo_q * scale, 4)
            point["upper"] = round(pred + hi_q * scale, 4)
        points.append(point)

        new_row = {time_column: next_time, target_column: pred, **carried}
        buffer = pd.concat([buffer, pd.DataFrame([new_row])], ignore_index=True)

    history = [
        {"time": t.isoformat(), "actual": round(float(v), 4)}
        for t, v in zip(df[time_column].tail(int(history_points)).tolist(), df[target_column].tail(int(history_points)).tolist())
    ]

    return {
        "target_column": target_column,
        "time_column": time_column,
        "steps": n_steps,
        "points": points,
        "history": history,
        "interval": {
            "level": 0.90,
            "method": "empirical residual quantiles (5th-95th percentile) from the held-out explain sample, "
                      "widened by sqrt(step) as recursive errors accumulate",
        } if lo_q is not None else None,
        "notes": notes,
    }
