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
from sklearn.metrics import (  # noqa: F401 - re-exported, scoring moved to automl_scoring
    accuracy_score, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error,
    precision_recall_fscore_support, r2_score,
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
from app.services.automl_forecast import (  # noqa: F401
    N_LAGS, _LAGS, _ROLLING_WINDOWS, _add_target_lag_features, forecast_trajectory, predict_with_model,
)
from app.services.automl_scoring import (  # noqa: F401
    _classification_diagnostics, _clean_params, _make_diagnostics, _pick_primary_metric, _regression_diagnostics,
    _score_classification, _score_regression, _scoring_for_task, normalize_higher_is_better,
)

# Categorical columns with more distinct values than this route through target
# encoding instead of one-hot, to avoid dimensionality blowup on high-cardinality
# columns (machine id, operator, SKU, ...).
_HIGH_CARDINALITY_THRESHOLD = 20


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
