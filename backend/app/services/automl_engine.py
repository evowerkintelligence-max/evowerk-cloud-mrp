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
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import loguniform
from scipy.stats import randint as sp_randint
from scipy.stats import uniform as sp_uniform
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
    StackingClassifier,
    StackingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
)
from sklearn.model_selection import (
    KFold,
    RandomizedSearchCV,
    StratifiedKFold,
    TimeSeriesSplit,
    cross_val_score,
    train_test_split,
)
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, TargetEncoder
from sklearn.svm import SVC, SVR, LinearSVC, LinearSVR

# Optional boosted-tree libs (highest ROI for tabular data, hence "industry
# standard" AutoML always includes them) - guarded so the app still runs on a
# platform where these heavy compiled deps failed to install.
try:
    import xgboost as xgb

    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

try:
    import lightgbm as lgb

    _HAS_LGBM = True
except ImportError:
    _HAS_LGBM = False

try:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _HAS_OPTUNA = True
except ImportError:
    optuna = None
    _HAS_OPTUNA = False

try:
    import shap

    _HAS_SHAP = True
except ImportError:
    shap = None
    _HAS_SHAP = False

# Parallelize the search across CV folds x param combos; tree estimators stay
# single-threaded (n_jobs=1 on each) to avoid nested oversubscription.
_SEARCH_N_JOBS = -1

# Categorical columns with more distinct values than this route through target
# encoding instead of one-hot, to avoid dimensionality blowup on high-cardinality
# columns (machine id, operator, SKU, ...).
_HIGH_CARDINALITY_THRESHOLD = 20

# Kernel SVM scales poorly; only offered as a roster option below this row count.
_KERNEL_SVM_MAX_ROWS = 3000

# Forecasting lag/rolling feature windows (of the target's own history).
_LAGS = (1, 2, 3, 7, 14)
_ROLLING_WINDOWS = (7, 14)
N_LAGS = max(_LAGS)


class AutoMLTask(str, Enum):
    classification = "classification"
    regression = "regression"
    forecasting = "forecasting"


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


def _normalize_higher_is_better(task: AutoMLTask, metric_value: float) -> float:
    """Flips lower-is-better metrics (rmse/mae) so every selection score compares as 'higher wins'."""
    return metric_value if task == AutoMLTask.classification else -metric_value


# =========================================================
# Model roster + hyperparameter search spaces
# =========================================================


def _param_distributions(model_name: str) -> dict[str, Any]:
    if model_name == "LogisticRegression":
        return {"model__C": loguniform(1e-2, 1e2)}
    if model_name == "Ridge":
        return {"model__alpha": loguniform(1e-2, 1e2)}
    if model_name == "ElasticNet":
        return {"model__alpha": loguniform(1e-3, 1e1), "model__l1_ratio": sp_uniform(0.0, 1.0)}
    if model_name in ("RandomForestClassifier", "RandomForestRegressor", "ExtraTreesClassifier", "ExtraTreesRegressor"):
        return {
            "model__n_estimators": sp_randint(80, 200),
            "model__max_depth": [8, 12, 20],
            "model__min_samples_leaf": sp_randint(2, 12),
        }
    if model_name in ("HistGradientBoostingClassifier", "HistGradientBoostingRegressor"):
        return {
            "model__learning_rate": loguniform(0.02, 0.3),
            "model__max_iter": sp_randint(150, 500),
            "model__max_leaf_nodes": sp_randint(15, 63),
            "model__l2_regularization": loguniform(1e-3, 1e1),
        }
    if model_name in ("KNeighborsClassifier", "KNeighborsRegressor"):
        return {
            "model__n_neighbors": sp_randint(3, 30),
            "model__weights": ["uniform", "distance"],
            "model__p": [1, 2],
        }
    if model_name in ("LinearSVC", "LinearSVR"):
        return {"model__C": loguniform(1e-2, 1e2)}
    if model_name in ("SVC", "SVR"):
        return {"model__C": loguniform(1e-1, 1e2), "model__gamma": loguniform(1e-3, 1e0)}
    if model_name in ("GradientBoostingClassifier", "GradientBoostingRegressor"):
        return {
            "model__n_estimators": sp_randint(80, 250),
            "model__learning_rate": loguniform(0.01, 0.2),
            "model__max_depth": sp_randint(2, 5),
        }
    if model_name in ("XGBoostClassifier", "XGBoostRegressor"):
        return {
            "model__n_estimators": sp_randint(200, 600),
            "model__learning_rate": loguniform(0.02, 0.2),
            "model__max_depth": sp_randint(3, 8),
            "model__subsample": sp_uniform(0.6, 0.4),
            "model__colsample_bytree": sp_uniform(0.6, 0.4),
        }
    if model_name in ("LightGBMClassifier", "LightGBMRegressor"):
        return {
            "model__n_estimators": sp_randint(200, 600),
            "model__learning_rate": loguniform(0.02, 0.2),
            "model__num_leaves": sp_randint(15, 127),
            "model__subsample": sp_uniform(0.6, 0.4),
            "model__colsample_bytree": sp_uniform(0.6, 0.4),
        }
    return {}  # StackingClassifier/StackingRegressor - base models are already tuned


def _optuna_param_space(trial: Any, model_name: str) -> dict[str, Any]:
    """Mirrors _param_distributions' ranges but sampled via Optuna's trial API
    for TPE-guided (Bayesian) search instead of independent random draws."""
    if model_name == "LogisticRegression":
        return {"model__C": trial.suggest_float("C", 1e-2, 1e2, log=True)}
    if model_name == "Ridge":
        return {"model__alpha": trial.suggest_float("alpha", 1e-2, 1e2, log=True)}
    if model_name == "ElasticNet":
        return {
            "model__alpha": trial.suggest_float("alpha", 1e-3, 1e1, log=True),
            "model__l1_ratio": trial.suggest_float("l1_ratio", 0.0, 1.0),
        }
    if model_name in ("RandomForestClassifier", "RandomForestRegressor", "ExtraTreesClassifier", "ExtraTreesRegressor"):
        return {
            "model__n_estimators": trial.suggest_int("n_estimators", 80, 200),
            "model__max_depth": trial.suggest_categorical("max_depth", [8, 12, 20]),
            "model__min_samples_leaf": trial.suggest_int("min_samples_leaf", 2, 12),
        }
    if model_name in ("HistGradientBoostingClassifier", "HistGradientBoostingRegressor"):
        return {
            "model__learning_rate": trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
            "model__max_iter": trial.suggest_int("max_iter", 150, 500),
            "model__max_leaf_nodes": trial.suggest_int("max_leaf_nodes", 15, 63),
            "model__l2_regularization": trial.suggest_float("l2_regularization", 1e-3, 1e1, log=True),
        }
    if model_name in ("KNeighborsClassifier", "KNeighborsRegressor"):
        return {
            "model__n_neighbors": trial.suggest_int("n_neighbors", 3, 30),
            "model__weights": trial.suggest_categorical("weights", ["uniform", "distance"]),
            "model__p": trial.suggest_categorical("p", [1, 2]),
        }
    if model_name in ("LinearSVC", "LinearSVR"):
        return {"model__C": trial.suggest_float("C", 1e-2, 1e2, log=True)}
    if model_name in ("SVC", "SVR"):
        return {
            "model__C": trial.suggest_float("C", 1e-1, 1e2, log=True),
            "model__gamma": trial.suggest_float("gamma", 1e-3, 1e0, log=True),
        }
    if model_name in ("GradientBoostingClassifier", "GradientBoostingRegressor"):
        return {
            "model__n_estimators": trial.suggest_int("n_estimators", 80, 250),
            "model__learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "model__max_depth": trial.suggest_int("max_depth", 2, 5),
        }
    if model_name in ("XGBoostClassifier", "XGBoostRegressor"):
        return {
            "model__n_estimators": trial.suggest_int("n_estimators", 200, 600),
            "model__learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "model__max_depth": trial.suggest_int("max_depth", 3, 8),
            "model__subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "model__colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        }
    if model_name in ("LightGBMClassifier", "LightGBMRegressor"):
        return {
            "model__n_estimators": trial.suggest_int("n_estimators", 200, 600),
            "model__learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "model__num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "model__subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "model__colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        }
    return {}


def _run_optuna_search(
    pipe: Any, model_name: str, X_sel: pd.DataFrame, y_sel: pd.Series, splitter: Any, scoring: str,
    n_trials: int, timeout: float | None, random_state: int,
) -> tuple[float, Any, dict[str, Any]]:
    """Bayesian (TPE) alternative to RandomizedSearchCV for one candidate: each
    trial clones the pipeline, samples params from _optuna_param_space, and
    scores it via the same CV splitter/scoring as random search. Returns
    (best_cv_score, fitted_best_pipeline, best_params_with_model_prefix)."""

    def _objective(trial: Any) -> float:
        params = _optuna_param_space(trial, model_name)
        candidate = clone(pipe)
        if params:
            candidate.set_params(**params)
        scores = cross_val_score(candidate, X_sel, y_sel, cv=splitter, scoring=scoring, n_jobs=_SEARCH_N_JOBS, error_score=np.nan)
        val = float(np.nanmean(scores))
        if not np.isfinite(val):
            raise optuna.TrialPruned()
        return val

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=random_state))
    study.optimize(_objective, n_trials=max(1, int(n_trials)), timeout=timeout, catch=(Exception,))

    if study.best_trial is None:
        raise RuntimeError("Optuna search produced no valid trial")

    best_params = {f"model__{k}": v for k, v in study.best_trial.params.items()}
    best_pipe = clone(pipe)
    if best_params:
        best_pipe.set_params(**best_params)
    best_pipe.fit(X_sel, y_sel)
    return float(study.best_trial.value), best_pipe, dict(study.best_trial.params)


def _all_models(
    task: AutoMLTask, random_state: int, class_weight_balanced: bool = False, scale_pos_weight: float | None = None
) -> dict[str, Any]:
    rs = random_state
    cw = "balanced" if class_weight_balanced else None
    if task == AutoMLTask.classification:
        models: dict[str, Any] = {
            "LogisticRegression": LogisticRegression(max_iter=1000, class_weight=cw),
            "RandomForestClassifier": RandomForestClassifier(n_estimators=250, random_state=rs, n_jobs=1, class_weight=cw),
            "ExtraTreesClassifier": ExtraTreesClassifier(n_estimators=300, random_state=rs, n_jobs=1, class_weight=cw),
            "HistGradientBoostingClassifier": HistGradientBoostingClassifier(random_state=rs, class_weight=cw),
            "GradientBoostingClassifier": GradientBoostingClassifier(random_state=rs),
            "KNeighborsClassifier": KNeighborsClassifier(),
            "LinearSVC": LinearSVC(random_state=rs, class_weight=cw),
            "SVC": SVC(random_state=rs, class_weight=cw),
        }
        if _HAS_XGB:
            models["XGBoostClassifier"] = xgb.XGBClassifier(
                n_estimators=400, learning_rate=0.05, max_depth=6, subsample=0.85,
                colsample_bytree=0.85, reg_lambda=1.0, random_state=rs, n_jobs=1,
                tree_method="hist", eval_metric="logloss",
                **({"scale_pos_weight": scale_pos_weight} if class_weight_balanced and scale_pos_weight else {}),
            )
        if _HAS_LGBM:
            models["LightGBMClassifier"] = lgb.LGBMClassifier(
                n_estimators=400, learning_rate=0.05, num_leaves=63, subsample=0.85,
                colsample_bytree=0.85, random_state=rs, n_jobs=1, verbose=-1, class_weight=cw,
            )
        return models
    models = {
        "Ridge": Ridge(),
        "ElasticNet": ElasticNet(max_iter=5000),
        "RandomForestRegressor": RandomForestRegressor(n_estimators=350, random_state=rs, n_jobs=1),
        "ExtraTreesRegressor": ExtraTreesRegressor(n_estimators=350, random_state=rs, n_jobs=1),
        "HistGradientBoostingRegressor": HistGradientBoostingRegressor(random_state=rs),
        "GradientBoostingRegressor": GradientBoostingRegressor(random_state=rs),
        "KNeighborsRegressor": KNeighborsRegressor(),
        "LinearSVR": LinearSVR(max_iter=5000, random_state=rs),
        "SVR": SVR(),
    }
    if _HAS_XGB:
        models["XGBoostRegressor"] = xgb.XGBRegressor(
            n_estimators=500, learning_rate=0.05, max_depth=6, subsample=0.85,
            colsample_bytree=0.85, reg_lambda=1.0, random_state=rs, n_jobs=1,
            tree_method="hist", eval_metric="rmse",
        )
    if _HAS_LGBM:
        models["LightGBMRegressor"] = lgb.LGBMRegressor(
            n_estimators=500, learning_rate=0.05, num_leaves=63, subsample=0.85,
            colsample_bytree=0.85, random_state=rs, n_jobs=1, verbose=-1,
        )
    return models


_PRETTY_MODEL_NAMES = {
    "LogisticRegression": "Logistic Regression",
    "Ridge": "Ridge Regression",
    "ElasticNet": "Elastic Net",
    "RandomForestClassifier": "Random Forest",
    "RandomForestRegressor": "Random Forest",
    "ExtraTreesClassifier": "Extra Trees",
    "ExtraTreesRegressor": "Extra Trees",
    "HistGradientBoostingClassifier": "Hist Gradient Boosting",
    "HistGradientBoostingRegressor": "Hist Gradient Boosting",
    "GradientBoostingClassifier": "Gradient Boosting",
    "GradientBoostingRegressor": "Gradient Boosting",
    "KNeighborsClassifier": "K-Nearest Neighbors",
    "KNeighborsRegressor": "K-Nearest Neighbors",
    "LinearSVC": "Linear SVM",
    "LinearSVR": "Linear SVM",
    "SVC": "Kernel SVM (RBF)",
    "SVR": "Kernel SVM (RBF)",
    "XGBoostClassifier": "XGBoost",
    "XGBoostRegressor": "XGBoost",
    "LightGBMClassifier": "LightGBM",
    "LightGBMRegressor": "LightGBM",
    "StackingClassifier": "Stacked Ensemble",
    "StackingRegressor": "Stacked Ensemble",
}


def _pretty_model_name(name: str) -> str:
    return _PRETTY_MODEL_NAMES.get(name, name)


def algorithm_roster(task: AutoMLTask) -> list[dict[str, str]]:
    """Selectable algorithms for a task (for the UI picker): id + friendly label."""
    return [{"id": n, "label": _pretty_model_name(n)} for n in _all_models(task, 42).keys()]


def _auto_roster_names(task: AutoMLTask, tier: str, n_train: int, available: set[str]) -> list[str]:
    """Ordered model names for an automatic (no explicit picker) run at a tier.
    KNN and SVMs are kept to the 'full' tier only: KNN's lazy prediction is
    expensive inside cross-validation, kernel SVM scales poorly."""
    if task == AutoMLTask.classification:
        core = ["LogisticRegression", "RandomForestClassifier"]
        boosters = [n for n in ("XGBoostClassifier", "LightGBMClassifier", "GradientBoostingClassifier") if n in available]
        std_extra = ["HistGradientBoostingClassifier"]
        full_extra = ["ExtraTreesClassifier", "KNeighborsClassifier", "LinearSVC"] + (
            ["SVC"] if n_train < _KERNEL_SVM_MAX_ROWS else []
        )
    else:
        core = ["Ridge", "RandomForestRegressor"]
        boosters = [n for n in ("XGBoostRegressor", "LightGBMRegressor", "GradientBoostingRegressor") if n in available]
        std_extra = ["ElasticNet", "HistGradientBoostingRegressor"]
        full_extra = ["ExtraTreesRegressor", "KNeighborsRegressor", "LinearSVR"] + (
            ["SVR"] if n_train < _KERNEL_SVM_MAX_ROWS else []
        )

    if tier == "core":
        names = core + boosters[:1]
    elif tier == "full":
        names = core + boosters + std_extra + full_extra
    else:
        names = core + boosters[:2] + std_extra
    return [n for n in dict.fromkeys(names) if n in available]


def _build_candidates(
    task: AutoMLTask,
    n_train: int,
    algorithms: list[str] | None,
    tier: str,
    random_state: int,
    class_weight_balanced: bool = False,
    scale_pos_weight: float | None = None,
) -> list[tuple[str, Any]]:
    models = _all_models(task, random_state, class_weight_balanced, scale_pos_weight)
    if algorithms:
        names = [a for a in algorithms if a in models]
        if not names:
            names = _auto_roster_names(task, tier, n_train, set(models))
    else:
        names = _auto_roster_names(task, tier, n_train, set(models))
    return [(n, models[n]) for n in names]


# Thoroughness profiles trade training time for search rigor: CV fold count,
# per-model randomized-search budget, the row cap for the search (winner is
# always refit on ALL rows), model roster tier, and stacked-ensemble on/off.
_THOROUGHNESS = {
    "fast": {"cv": 3, "n_iter": 5, "sample_cap": 4000, "tier": "core", "stacking": False},
    "balanced": {"cv": 5, "n_iter": 8, "sample_cap": 6000, "tier": "standard", "stacking": False},
    "thorough": {"cv": 5, "n_iter": 15, "sample_cap": 10000, "tier": "full", "stacking": True},
}


def _thoroughness_profile(name: str | None) -> dict[str, Any]:
    return dict(_THOROUGHNESS.get(str(name or "balanced").lower().strip(), _THOROUGHNESS["balanced"]))


def _selection_subsample(
    task: AutoMLTask, X_train: pd.DataFrame, y_train: pd.Series, random_state: int, sample_cap: int
) -> tuple[pd.DataFrame, pd.Series, bool]:
    n = len(X_train)
    if n <= sample_cap:
        return X_train, y_train, False
    if task == AutoMLTask.forecasting:
        return X_train.iloc[-sample_cap:], y_train.iloc[-sample_cap:], True

    strat = None
    if task == AutoMLTask.classification:
        try:
            if y_train.value_counts().min() >= 2:
                strat = y_train
        except Exception:
            strat = None
    try:
        X_sel, _, y_sel, _ = train_test_split(
            X_train, y_train, train_size=sample_cap, random_state=random_state, shuffle=True, stratify=strat
        )
    except Exception:
        X_sel, _, y_sel, _ = train_test_split(
            X_train, y_train, train_size=sample_cap, random_state=random_state, shuffle=True, stratify=None
        )
    return X_sel, y_sel, True


def _cv_plan(
    task: AutoMLTask, n_train: int, y_train: pd.Series | None, random_state: int, profile: dict[str, Any]
) -> tuple[Any, int, int]:
    """Decide the CV splitter + search budget. Returns (splitter, cv_folds, n_iter);
    splitter is None when the training set is too small for CV."""
    if n_train < 40:
        return None, 0, 0

    cv_folds = int(profile.get("cv", 5))
    n_iter = int(profile.get("n_iter", 8))
    if n_train > 100_000:
        cv_folds, n_iter = min(cv_folds, 3), min(n_iter, 5)
    elif n_train > 20_000:
        cv_folds, n_iter = min(cv_folds, 4), min(n_iter, 8)

    if task == AutoMLTask.forecasting:
        return TimeSeriesSplit(n_splits=cv_folds), cv_folds, n_iter

    if task == AutoMLTask.classification and y_train is not None:
        min_class = int(y_train.value_counts().min())
        if min_class >= 2:
            folds = max(2, min(cv_folds, min_class))
            return StratifiedKFold(n_splits=folds, shuffle=True, random_state=random_state), folds, n_iter
        return KFold(n_splits=cv_folds, shuffle=True, random_state=random_state), cv_folds, n_iter

    return KFold(n_splits=cv_folds, shuffle=True, random_state=random_state), cv_folds, n_iter


# =========================================================
# Forecasting: lag/rolling/calendar features of the target's own history
# =========================================================


def _add_target_lag_features(df: pd.DataFrame, time_col: str, target_col: str) -> tuple[pd.DataFrame, list[str]]:
    """Assumes df is already sorted by time_col. Returns (df_with_features, new_column_names)."""
    out = df.copy()
    new_cols: list[str] = []
    s = out[target_col]

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
            selection_score = _normalize_higher_is_better(task, mv)
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
                "selection_score": _normalize_higher_is_better(task, mv),
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
