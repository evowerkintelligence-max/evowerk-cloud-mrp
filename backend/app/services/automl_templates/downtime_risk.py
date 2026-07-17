"""Downtime Risk AutoML template. Default (regression) framing predicts how
long a downtime incident of a given machine/reason/shift profile is likely
to last, using the exact column shape app/services/downtime_tracking.py's
materialized live dataset already produces (machine, reason, shift,
duration_min) - a thin wrapper over the shared simple-regression template
helper, same as v1.

A second task-framing option, "classification" ("will there be an unplanned
incident next shift"), buckets rows into daily periods, derives a
next-period binary target, and routes it through automl_rare_event.py
(unplanned incidents are typically rare) plus timeseries_quality.py, since
this framing needs date_col to order periods chronologically. That framing
doesn't fit _shared.py's regression-only, one-row-per-incident helpers, so
it's bespoke here - see the _classification_* functions below.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import automl_rare_event, timeseries_quality
from app.services.automl_templates import AutoMLTemplate, ColumnRole, PreprocessResult, register_template
from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess

ROLES = [
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("reason_col", "Downtime reason column"),
    ColumnRole("shift_col", "Shift column"),
    ColumnRole("duration_col", "Duration (minutes)"),
    ColumnRole("date_col", "Date column", required=False),
]

# Exactly machine/reason/shift as features - not "all other columns" mode,
# since a materialized downtime dataset's only other column (`planned`) is
# a deterministic function of `reason` and would be leakage/noise, not
# signal, if included.
_regression_preprocess = make_simple_regression_preprocess(
    target_role="duration_col",
    required_roles=["machine_col", "reason_col", "shift_col", "duration_col"],
    explicit_feature_roles=["machine_col", "reason_col", "shift_col"],
)

_regression_postprocess = make_simple_regression_postprocess(summary_key="downtime_summary")

_PERIOD_FEATURE_COLS = ["incident_count", "total_duration_min", "avg_duration_min", "n_machines_affected"]


def _period_features(df: pd.DataFrame, date_col: str, machine_col: str, duration_col: str) -> pd.DataFrame:
    """One row per calendar day between the first and last observed incident,
    inclusive. A day with >= 1 downtime incident gets real aggregate values;
    a day with zero incidents is explicitly zero-filled (not left as NaN) -
    for this count/duration aggregate, "day is absent from the raw log"
    means "zero incidents that day", a real value, not a missing measurement
    to interpolate. That's a different gap semantic than a sensor stream, so
    it's handled here rather than left to timeseries_quality's generic
    NaN-then-interpolate handling (which still runs afterward for outlier
    smoothing).
    ponytail: buckets by day only, not day+shift - shift-level bucketing
    would need a synthetic shift-ordering rule; add if daily granularity
    proves too coarse."""
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=[date_col])
    work[duration_col] = pd.to_numeric(work[duration_col], errors="coerce").fillna(0.0)
    work["_period_"] = work[date_col].dt.floor("D")

    grouped = work.groupby("_period_").agg(
        incident_count=(duration_col, "count"),
        total_duration_min=(duration_col, "sum"),
        avg_duration_min=(duration_col, "mean"),
        n_machines_affected=(machine_col, pd.Series.nunique),
    )
    full_range = pd.date_range(grouped.index.min(), grouped.index.max(), freq="D")
    grouped = grouped.reindex(full_range, fill_value=0.0)
    grouped.index.name = date_col
    return grouped.reset_index()


def _classification_preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    cols = config.get("column_map", {})
    required = ["machine_col", "reason_col", "shift_col", "duration_col", "date_col"]
    missing = [r for r in required if not cols.get(r)]
    if missing:
        raise ValueError(
            "Classification framing ('will there be an unplanned incident next shift') needs a date "
            f"column to order periods chronologically. Missing required column mapping for: {', '.join(missing)}"
        )
    for role_key, col_name in cols.items():
        if col_name and col_name not in df.columns:
            raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

    date_col, machine_col, duration_col = cols["date_col"], cols["machine_col"], cols["duration_col"]

    period_df = _period_features(df, date_col, machine_col, duration_col)
    if len(period_df) < 20:
        raise ValueError(
            f"Not enough distinct time periods to build a next-period target ({len(period_df)}, need >= 20)"
        )

    period_df, quality_notes, excluded_cols = timeseries_quality.clean_timeseries(
        period_df, date_col, _PERIOD_FEATURE_COLS,
    )
    feature_cols = [c for c in _PERIOD_FEATURE_COLS if c not in excluded_cols]
    if not feature_cols:
        raise ValueError("No usable feature columns remain after time-series cleaning")

    # Target = "does the NEXT period have any incident" - a period whose next
    # value is unknown (last row, or the next period fell inside a long gap
    # timeseries_quality left as NaN) is dropped rather than guessed at.
    future_count = period_df["incident_count"].shift(-1)
    target_col = "_incident_next_period_"
    period_df[target_col] = np.where(future_count.isna(), np.nan, (future_count > 0).astype(float))
    period_df = period_df.dropna(subset=[target_col])
    period_df[target_col] = period_df[target_col].astype(int)

    if len(period_df) < 20:
        raise ValueError(
            f"Not enough periods with a known next-period outcome ({len(period_df)}, need >= 20)"
        )

    notes = [
        "Classification framing: predicting whether the next daily period has any unplanned incident, "
        f"across {len(period_df)} period(s)."
    ]
    return automl_rare_event.route_rare_event(
        period_df, target_col, feature_cols,
        min_positive_examples=config.get("min_positive_examples", 20),
        notes=notes,
        context={"target_col": target_col, "data_quality_notes": quality_notes},
    )


def _classification_postprocess(
    engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    engine_result = engine_result or context.get("engine_result")
    if engine_result is None:
        raise RuntimeError(
            "Classification-framing downtime preprocess should always produce an engine_result, either "
            "directly (classification route) or via context['engine_result'] (anomaly route)."
        )
    meta = engine_result["meta"]
    summary = {
        "target_column": context["target_col"],
        "best_model": meta["model_name"],
        "metric_name": meta["metric_name"],
        "metric_value": meta["metric_value"],
        "rows_used": meta["rows_used"],
        "rare_event_routing": context.get("rare_event_routing"),
    }
    return {
        "downtime_summary": summary,
        "pipeline": engine_result["pipeline"],
        "explain_X": engine_result["explain_X"],
        "explain_y": engine_result["explain_y"],
        "leaderboard": engine_result["leaderboard"],
        "best_model_name": meta["model_name"],
        "metric_name": meta["metric_name"],
        "metric_value": meta["metric_value"],
        "data_quality_notes": context.get("data_quality_notes", []),
    }


def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    if str(config.get("task_framing", "regression")).lower().strip() == "classification":
        return _classification_preprocess(df, config)
    return _regression_preprocess(df, config)


def postprocess(
    engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    if context.get("rare_event_routing") is not None:
        return _classification_postprocess(engine_result, config, context)
    return _regression_postprocess(engine_result, config, context)


register_template(AutoMLTemplate(
    id="downtime_risk",
    label="Downtime Risk",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=False,
))
