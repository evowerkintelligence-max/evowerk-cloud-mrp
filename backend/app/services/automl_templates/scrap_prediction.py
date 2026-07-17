"""Scrap Prediction AutoML template: predicts scrap/defect units from
whatever process-parameter columns exist in the dataset alongside the
mapped total-units/scrap columns. No legacy ML behavior to preserve -
scrap.py is pure SQL aggregation - so the default (regression) framing is a
thin wrapper over the shared simple-regression template helper.

A second task-framing option, "classification" ("will this lot/run scrap"),
derives a binary target instead and routes it through automl_rare_event.py
(scrap events are typically rare) plus timeseries_quality.py when a date_col
is mapped. That framing doesn't fit _shared.py's regression-only helpers, so
it's bespoke here - see the two _classification_* functions below.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.services import automl_rare_event, timeseries_quality
from app.services.automl_templates import AutoMLTemplate, ColumnRole, PreprocessResult, register_template
from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess

ROLES = [
    ColumnRole("total_units_col", "Total units"),
    ColumnRole("scrap_col", "Scrap units"),
    ColumnRole("reason_col", "Rejection reason column", required=False),
    ColumnRole("machine_col", "Machine column", required=False),
    ColumnRole("date_col", "Date column", required=False),
]

# total_units_col is excluded from features (it's a normalization
# denominator, not a predictor) and reason_col is excluded because it only
# has a value once scrap already happened - using it as a feature would be
# leakage. Every other column in the dataset (machine, date, and any
# process-parameter columns the user didn't explicitly map) becomes a
# candidate feature.
_regression_preprocess = make_simple_regression_preprocess(
    target_role="scrap_col",
    required_roles=["total_units_col", "scrap_col"],
    excluded_from_features=["total_units_col", "reason_col"],
)

_regression_postprocess = make_simple_regression_postprocess(summary_key="scrap_summary")


def _classification_preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    cols = config.get("column_map", {})
    required = ["total_units_col", "scrap_col"]
    missing = [r for r in required if not cols.get(r)]
    if missing:
        raise ValueError(f"Missing required column mapping for: {', '.join(missing)}")
    for role_key, col_name in cols.items():
        if col_name and col_name not in df.columns:
            raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

    scrap_col = cols["scrap_col"]
    total_units_col = cols["total_units_col"]
    reason_col = cols.get("reason_col")
    date_col = cols.get("date_col")

    excluded_from_features = {c for c in (total_units_col, reason_col, scrap_col) if c}
    feature_cols = [c for c in df.columns if c not in excluded_from_features]
    if not feature_cols:
        raise ValueError("No usable feature columns remain after excluding target/leakage columns")

    work = df[[scrap_col, *feature_cols]].copy()
    work[scrap_col] = pd.to_numeric(work[scrap_col], errors="coerce")
    work = work.dropna(subset=[scrap_col])
    if len(work) < 20:
        raise ValueError(f"Not enough rows with a valid '{scrap_col}' value ({len(work)}, need >= 20)")

    target_col = "_will_scrap_"
    work[target_col] = (work[scrap_col] > 0).astype(int)

    notes = [
        f"Classification framing: predicting whether a lot/run scraps at all "
        f"(derived from '{scrap_col}' > 0) across {len(work)} rows."
    ]

    quality_notes: list[str] = []
    if date_col and date_col in work.columns:
        numeric_candidates = [
            c for c in feature_cols
            if c not in (date_col, scrap_col) and pd.api.types.is_numeric_dtype(work[c])
        ]
        if numeric_candidates:
            work, quality_notes, excluded_cols = timeseries_quality.clean_timeseries(
                work, date_col, numeric_candidates, resample=False,
            )
            feature_cols = [c for c in feature_cols if c not in excluded_cols]

    return automl_rare_event.route_rare_event(
        work, target_col, feature_cols,
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
            "Classification-framing scrap preprocess should always produce an engine_result, either "
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
        "scrap_summary": summary,
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
    id="scrap_prediction",
    label="Scrap Prediction",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=False,
))
