"""Scrap Prediction AutoML template: predicts scrap/defect units from
whatever process-parameter columns exist in the dataset alongside the
mapped total-units/scrap columns. No legacy ML behavior to preserve -
scrap.py is pure SQL aggregation - so this is a thin wrapper over the
shared simple-regression template helper.
"""
from __future__ import annotations

from app.services.automl_templates import AutoMLTemplate, ColumnRole, register_template
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
preprocess = make_simple_regression_preprocess(
    target_role="scrap_col",
    required_roles=["total_units_col", "scrap_col"],
    excluded_from_features=["total_units_col", "reason_col"],
)

postprocess = make_simple_regression_postprocess(summary_key="scrap_summary")

register_template(AutoMLTemplate(
    id="scrap_prediction",
    label="Scrap Prediction",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=False,
))
