"""Predictive Maintenance AutoML template: re-platforms predictive.py's
old hardcoded GradientBoostingClassifier onto the shared automl_engine
bake-off, while keeping its RUL/cost/recommendation math and statistical
fallback for machines with too little history to train on.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import predictive as legacy
from app.services.automl_templates import ColumnRole, PreprocessResult

_W, _H, _MIN_ROWS = 7, 3, 40


def _days_since(fail_arr: np.ndarray, up_to: int) -> float:
    idxs = np.where(fail_arr[:up_to] == 1)[0]
    return float(up_to - idxs[-1]) if len(idxs) > 0 else 60.0


def build_training_frame(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    """Same rolling-window featurization as the old _ml_analyze, ported to
    produce a flat DataFrame for automl_engine instead of numpy arrays fed
    directly to a hardcoded model. `daily` must have columns machine, date,
    oee, scrap_rate, dt_rate, is_failure (sorted per-machine by date is not
    required - this function sorts internally)."""
    rows: list[dict[str, float]] = []
    last_x: dict[str, list[float]] = {}

    for machine, mdf in daily.groupby("machine"):
        mdf = mdf.sort_values("date").reset_index(drop=True)
        n = len(mdf)
        if n < _W + _H + 1:
            continue

        oee = mdf["oee"].to_numpy(dtype=float)
        scr = mdf["scrap_rate"].to_numpy(dtype=float)
        dtr = mdf["dt_rate"].to_numpy(dtype=float)
        fail = mdf["is_failure"].to_numpy(dtype=int)

        for i in range(_W, n - _H):
            ow = oee[i - _W:i]
            rows.append({
                "oee_7d": float(np.mean(ow)),
                "oee_slope_7d": legacy._linreg_slope(ow.tolist()),
                "scrap_rate_7d": float(np.mean(scr[i - _W:i])),
                "dt_rate_7d": float(np.mean(dtr[i - _W:i])),
                "fail_count_7d": float(np.sum(fail[i - _W:i])),
                "days_since_fail": _days_since(fail, i),
                "is_failure_next": 1 if np.sum(fail[i:i + _H]) > 0 else 0,
            })

        ow = oee[n - _W:n]
        last_x[str(machine)] = [
            float(np.mean(ow)), legacy._linreg_slope(ow.tolist()),
            float(np.mean(scr[n - _W:n])), float(np.mean(dtr[n - _W:n])),
            float(np.sum(fail[n - _W:n])), _days_since(fail, n),
        ]

    columns = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail", "is_failure_next"]
    training_df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    return training_df, last_x


def has_ml_signal(training_df: pd.DataFrame, min_rows: int = _MIN_ROWS) -> bool:
    if len(training_df) < min_rows:
        return False
    pos_rate = float(training_df["is_failure_next"].mean())
    return 0.05 <= pos_rate <= 0.90


ROLES = [
    ColumnRole("date_col", "Date column"),
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("planned_time_col", "Planned time"),
    ColumnRole("operating_time_col", "Operating time"),
    ColumnRole("total_units_col", "Total units"),
    ColumnRole("good_units_col", "Good units"),
    ColumnRole("failure_col", "Failure flag column", required=False),
    ColumnRole("downtime_category_col", "Downtime category", required=False),
]

# Excludes SVC/LinearSVC - automl_engine's SVC has no probability=True, so it
# has no .predict_proba(), which postprocess() needs for a risk score, not
# just a class label.
_SAFE_ALGORITHMS = [
    "LogisticRegression", "RandomForestClassifier", "ExtraTreesClassifier",
    "HistGradientBoostingClassifier", "GradientBoostingClassifier", "KNeighborsClassifier",
    "XGBoostClassifier", "LightGBMClassifier",
]


def _merge_failure_flag(daily_ml: pd.DataFrame, df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    """OR the optional per-row failure_col (a 0/1 flag, distinct from
    _build_daily's category-name-based downtime_category_col) into
    daily_ml["is_failure"], so build_training_frame's is_failure_next has a
    ground-truth signal even when no downtime_category_col is mapped."""
    failure_col = cols.get("failure_col")
    if not failure_col or failure_col not in df.columns:
        return daily_ml

    fdf = df[[cols["machine_col"], cols["date_col"], failure_col]].copy()
    fdf[cols["date_col"]] = pd.to_datetime(fdf[cols["date_col"]], errors="coerce")
    fdf = fdf.dropna(subset=[cols["date_col"]])
    fdf["_flag"] = pd.to_numeric(fdf[failure_col], errors="coerce").fillna(0) > 0
    flag_daily = (
        fdf.groupby([cols["machine_col"], pd.Grouper(key=cols["date_col"], freq="D")])["_flag"]
        .max().reset_index()
        .rename(columns={cols["machine_col"]: "machine", cols["date_col"]: "date", "_flag": "_flag_fail"})
    )
    daily_ml = daily_ml.merge(flag_daily, on=["machine", "date"], how="left")
    daily_ml["is_failure"] = (daily_ml["is_failure"].astype(bool) | daily_ml["_flag_fail"].fillna(False)).astype(int)
    return daily_ml.drop(columns=["_flag_fail"])


def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    cols = config.get("column_map", {})
    required = ["date_col", "machine_col", "planned_time_col", "operating_time_col", "total_units_col", "good_units_col"]
    missing_roles = [r for r in required if not cols.get(r)]
    if missing_roles:
        raise ValueError(f"Missing required column mapping for: {', '.join(missing_roles)}")
    for role_key, col_name in cols.items():
        if col_name and col_name not in df.columns:
            raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

    grouped, daily_ml, days_per_period = legacy._build_daily(
        df, date_col=cols["date_col"], machine_col=cols["machine_col"],
        planned_time_col=cols["planned_time_col"], operating_time_col=cols["operating_time_col"],
        total_units_col=cols["total_units_col"], good_units_col=cols["good_units_col"],
        target_rate=config.get("target_rate"), downtime_category_col=cols.get("downtime_category_col"),
    )
    daily_ml = _merge_failure_flag(daily_ml, df, cols)

    training_df, last_x = build_training_frame(daily_ml)
    context = {
        "grouped": grouped, "daily_ml": daily_ml, "days_per_period": days_per_period,
        "last_x": last_x, "column_map": cols, "hourly_value": config.get("hourly_value", 1000.0),
        "n_training_rows": len(training_df),
        "failure_col": cols.get("failure_col"), "downtime_category_col": cols.get("downtime_category_col"),
    }

    if not has_ml_signal(training_df):
        return PreprocessResult(
            skip_ml=True,
            notes=[f"Not enough rolling-window history to train a classifier ({len(training_df)} usable "
                   f"windows, need >= {_MIN_ROWS} with a 5-90% failure rate) - using the statistical risk model instead."],
            context=context,
        )

    return PreprocessResult(
        skip_ml=False,
        df=training_df,
        target_col="is_failure_next",
        task="classification",
        feature_cols=["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"],
        algorithms=_SAFE_ALGORITHMS,
        notes=[f"Trained on {len(training_df)} rolling 7-day windows across all machines."],
        context=context,
    )
