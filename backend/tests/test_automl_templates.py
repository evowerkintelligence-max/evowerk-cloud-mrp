from __future__ import annotations

import pytest
import pandas as pd

from app.services import automl_templates as templates
from app.services.automl_templates import predictive_maintenance as pm


def test_register_and_get_template():
    t = templates.AutoMLTemplate(
        id="_test_template",
        label="Test Template",
        roles=[templates.ColumnRole("x_col", "X column")],
        preprocess=lambda df, config: templates.PreprocessResult(),
        postprocess=lambda engine_result, config, context: {},
    )
    templates.register_template(t)
    try:
        fetched = templates.get_template("_test_template")
        assert fetched is t
        assert any(x.id == "_test_template" for x in templates.list_templates())
    finally:
        del templates._REGISTRY["_test_template"]


def test_get_unknown_template_raises():
    with pytest.raises(KeyError, match="Unknown AutoML template"):
        templates.get_template("_does_not_exist")


def _daily_fixture() -> pd.DataFrame:
    # One machine, 15 days: OEE steady at 0.8 for days 1-10, then a failure
    # on day 11 and degraded OEE (0.3) for days 11-15 - long enough to
    # produce rolling windows (W=7, H=3 -> needs >= 11 days).
    rows = []
    for day in range(1, 16):
        oee = 0.8 if day <= 10 else 0.3
        is_failure = 1 if day == 11 else 0
        rows.append({
            "machine": "M1", "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1),
            "oee": oee, "scrap_rate": 1 - oee, "dt_rate": 1 - oee, "is_failure": is_failure,
        })
    return pd.DataFrame(rows)


def test_build_training_frame_produces_expected_columns_and_row_count():
    daily = _daily_fixture()
    training_df, last_by_machine = pm.build_training_frame(daily)

    # n=15, W=7, H=3 -> range(7, 15-3) = range(7, 12) = 5 rows for M1.
    assert len(training_df) == 5
    assert set(training_df.columns) == {
        "oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d",
        "fail_count_7d", "days_since_fail", "is_failure_next",
    }
    assert "M1" in last_by_machine
    assert len(last_by_machine["M1"]) == 6


def test_build_training_frame_skips_machines_with_too_little_history():
    daily = _daily_fixture()
    short = daily[daily["date"] < pd.Timestamp("2026-01-08")]  # only 7 days: n < W+H+1=11
    training_df, last_by_machine = pm.build_training_frame(short)
    assert len(training_df) == 0  # no full W+H window fits, so zero training rows
    assert "M1" in last_by_machine  # n=7 >= W=7, so a "last window" snapshot still exists


def test_has_ml_signal_requires_min_rows_and_balanced_positive_rate():
    enough_balanced = pd.DataFrame({"is_failure_next": [0, 1] * 25})  # 50 rows, 50% positive
    assert pm.has_ml_signal(enough_balanced) is True

    too_few = pd.DataFrame({"is_failure_next": [0, 1] * 5})  # 10 rows
    assert pm.has_ml_signal(too_few) is False

    all_positive = pd.DataFrame({"is_failure_next": [1] * 50})  # pos_rate=1.0 > 0.90
    assert pm.has_ml_signal(all_positive) is False
