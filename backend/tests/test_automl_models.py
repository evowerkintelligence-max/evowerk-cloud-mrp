from __future__ import annotations

import pandas as pd
from sklearn.model_selection import StratifiedKFold, TimeSeriesSplit

from app.services.automl_models import (
    AutoMLTask,
    _cv_plan,
    _thoroughness_profile,
    algorithm_roster,
)


def test_algorithm_roster_has_friendly_labels():
    roster = {r["id"]: r["label"] for r in algorithm_roster(AutoMLTask.regression)}
    assert roster["Ridge"] == "Ridge Regression"
    assert roster["SVR"] == "Kernel SVM (RBF)"


def test_cv_plan_picks_splitter_per_task():
    profile = _thoroughness_profile("fast")
    assert _cv_plan(AutoMLTask.regression, 39, None, 42, profile) == (None, 0, 0)

    splitter, folds, n_iter = _cv_plan(AutoMLTask.forecasting, 100, None, 42, profile)
    assert isinstance(splitter, TimeSeriesSplit) and (folds, n_iter) == (3, 5)

    y = pd.Series([0] * 98 + [1] * 2)  # minority class of 2 caps folds at 2
    splitter, folds, _ = _cv_plan(AutoMLTask.classification, 100, y, 42, profile)
    assert isinstance(splitter, StratifiedKFold) and folds == 2


def test_unknown_thoroughness_falls_back_to_balanced():
    assert _thoroughness_profile("nope") == _thoroughness_profile("balanced")
