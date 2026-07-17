"""Downtime Risk AutoML template v1: predicts how long a downtime incident
of a given machine/reason/shift profile is likely to last, using the exact
column shape app/services/downtime_tracking.py's materialized live dataset
already produces (machine, reason, shift, duration_min).

Deliberately NOT "predict an incident before it happens" (the fuller
design-spec framing) - that needs rolling-window time-series featurization
and rare-event handling, out of scope here. See the plan's scope note.
"""
from __future__ import annotations

from app.services.automl_templates import AutoMLTemplate, ColumnRole, RecommendationRule, register_template
from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess

# All three candidate features (machine/reason/shift) are categorical, so
# automl_explain_nl.py's driver direction always comes back "high" for them
# (there's no numeric "low" reading for a category) - rules here are written
# against that "high" direction.
RECOMMENDATION_RULES = [
    RecommendationRule("reason", "high", "Downtime reason code is the strongest driver of incident length - review the top reason category's countermeasure/SOP and confirm it's being followed."),
    RecommendationRule("machine", "high", "Downtime duration varies most by machine - investigate whether the flagged machine needs maintenance or operator retraining."),
    RecommendationRule("shift", "high", "Downtime duration varies by shift - check staffing levels, handover procedures, or shift-specific process differences."),
]

ROLES = [
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("reason_col", "Downtime reason column"),
    ColumnRole("shift_col", "Shift column"),
    ColumnRole("duration_col", "Duration (minutes)"),
]

# Exactly machine/reason/shift as features - not "all other columns" mode,
# since a materialized downtime dataset's only other column (`planned`) is
# a deterministic function of `reason` and would be leakage/noise, not
# signal, if included.
preprocess = make_simple_regression_preprocess(
    target_role="duration_col",
    required_roles=["machine_col", "reason_col", "shift_col", "duration_col"],
    explicit_feature_roles=["machine_col", "reason_col", "shift_col"],
)

postprocess = make_simple_regression_postprocess(summary_key="downtime_summary")

register_template(AutoMLTemplate(
    id="downtime_risk",
    label="Downtime Risk",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    recommendation_rules=RECOMMENDATION_RULES,
    is_timeseries=False,
))
