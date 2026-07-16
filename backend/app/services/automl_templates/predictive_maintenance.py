"""Predictive Maintenance AutoML template: re-platforms predictive.py's
old hardcoded GradientBoostingClassifier onto the shared automl_engine
bake-off, while keeping its RUL/cost/recommendation math and statistical
fallback for machines with too little history to train on.
"""
from __future__ import annotations


import numpy as np
import pandas as pd

from app.services import predictive as legacy

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
