"""AutoML task enum, model roster, and hyperparameter search spaces
(random + Optuna/Bayesian), plus CV / subsample planning for
automl_engine. Split out of automl_engine.py."""
from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import loguniform
from scipy.stats import randint as sp_randint
from scipy.stats import uniform as sp_uniform
from sklearn.base import clone
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.model_selection import (
    KFold,
    StratifiedKFold,
    TimeSeriesSplit,
    cross_val_score,
    train_test_split,
)
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
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

# Parallelize the search across CV folds x param combos; tree estimators stay
# single-threaded (n_jobs=1 on each) to avoid nested oversubscription.
_SEARCH_N_JOBS = -1

# Kernel SVM scales poorly; only offered as a roster option below this row count.
_KERNEL_SVM_MAX_ROWS = 3000


class AutoMLTask(str, Enum):
    classification = "classification"
    regression = "regression"
    forecasting = "forecasting"


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
