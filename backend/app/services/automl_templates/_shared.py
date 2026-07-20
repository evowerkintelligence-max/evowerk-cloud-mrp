"""Shared helpers for simple row-level regression templates (Scrap
Prediction, Downtime Risk) - templates with no bespoke legacy math to
preserve: map columns, predict a numeric target from the rest, summarize
the winning model. Predictive Maintenance does NOT use this - it has real
legacy RUL/cost/statistical-fallback math that needs its own preprocess/
postprocess (see automl_templates/predictive_maintenance.py).
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from app.services.automl_templates import PreprocessResult


def make_simple_regression_preprocess(
    *,
    target_role: str,
    required_roles: list[str],
    excluded_from_features: list[str] = (),
    explicit_feature_roles: list[str] | None = None,
    min_rows: int = 20,
) -> Callable[[pd.DataFrame, dict[str, Any]], PreprocessResult]:
    """Returns a preprocess() that predicts the numeric column mapped to
    `target_role` from either every other unmapped dataset column
    (explicit_feature_roles=None) or exactly the columns mapped to
    `explicit_feature_roles`. `excluded_from_features` names roles (e.g. a
    column that only exists once the outcome already happened - leakage)
    to keep out of the "all other columns" feature set; ignored in
    explicit-roles mode, where the feature set is already an exact list."""

    def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
        cols = config.get("column_map", {})
        missing = [r for r in required_roles if not cols.get(r)]
        if missing:
            raise ValueError(f"Missing required column mapping for: {', '.join(missing)}")
        for role_key, col_name in cols.items():
            if col_name and col_name not in df.columns:
                raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

        target_col = cols[target_role]

        if explicit_feature_roles is not None:
            feature_cols = [cols[r] for r in explicit_feature_roles if cols.get(r)]
        else:
            excluded_cols = {cols[r] for r in excluded_from_features if cols.get(r)} | {target_col}
            feature_cols = [c for c in df.columns if c not in excluded_cols]

        if not feature_cols:
            raise ValueError("No usable feature columns remain after excluding target/leakage columns")

        work = df[[target_col, *feature_cols]].copy()
        work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
        work = work.dropna(subset=[target_col])
        if len(work) < min_rows:
            raise ValueError(
                f"Not enough rows with a valid '{target_col}' value ({len(work)}, need >= {min_rows})"
            )

        return PreprocessResult(
            skip_ml=False,
            df=work,
            target_col=target_col,
            task="regression",
            feature_cols=feature_cols,
            notes=[f"Predicting '{target_col}' from {len(feature_cols)} feature column(s) across {len(work)} rows."],
            context={"target_col": target_col},
        )

    return preprocess


def make_simple_regression_postprocess(
    *, summary_key: str,
) -> Callable[[dict[str, Any] | None, dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """Returns a postprocess() that summarizes the winning model into a
    small `{summary_key: {...}}` dict - no bespoke domain math, since these
    templates have no legacy behavior to preserve."""

    def postprocess(
        engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        if engine_result is None:
            raise RuntimeError(
                "Simple regression templates never set skip_ml=True, so postprocess should always receive an engine_result"
            )
        meta = engine_result["meta"]
        summary = {
            "target_column": context["target_col"],
            "best_model": meta["model_name"],
            "metric_name": meta["metric_name"],
            "metric_value": meta["metric_value"],
            "rows_used": meta["rows_used"],
        }
        return {
            summary_key: summary,
            "pipeline": engine_result["pipeline"],
            "explain_X": engine_result["explain_X"],
            "explain_y": engine_result["explain_y"],
            "leaderboard": engine_result["leaderboard"],
            "best_model_name": meta["model_name"],
            "metric_name": meta["metric_name"],
            "metric_value": meta["metric_value"],
        }

    return postprocess
