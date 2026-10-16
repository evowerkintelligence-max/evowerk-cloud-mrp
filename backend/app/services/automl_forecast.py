"""Forecasting lag/rolling/calendar features, prediction and the recursive
multi-step forecast trajectory for automl_engine. Split out of
automl_engine.py; pure functions over in-memory pandas objects."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Forecasting lag/rolling feature windows (of the target's own history).
_LAGS = (1, 2, 3, 7, 14)
_ROLLING_WINDOWS = (7, 14)
N_LAGS = max(_LAGS)


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
