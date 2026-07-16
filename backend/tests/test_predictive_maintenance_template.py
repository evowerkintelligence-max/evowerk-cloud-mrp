from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import predictive_maintenance as pm

# M1: healthy at 0.8 OEE for 30 days. M2: healthy for 10 days, then 20 days
# of failures/low OEE - enough history for the rolling-window featurizer to
# produce >= 40 training rows with a balanced-ish positive rate. The training
# target (daily_ml["is_failure"]) is driven by downtime_category_col
# (matched against legacy._UNPLANNED_CATS), matching predictive.py's
# _build_daily exactly - failure_col is a separate, legitimate-but-unused-
# here column that only feeds analyze()'s RUL "last failure days ago"
# lookback, never the ML training target.
_ROWS = []
for day in range(1, 31):
    _ROWS.append(f"2026-01-{day:02d},M1,480,470,920,900,,")
    is_fail_day = day in (11, 18, 25)
    fail = "1" if is_fail_day else ""
    category = "Mechanical Failure" if is_fail_day else ""
    operating = 150 if day >= 11 else 470
    total = 200 if day >= 11 else 920
    good = 150 if day >= 11 else 900
    _ROWS.append(f"2026-01-{day:02d},M2,480,{operating},{total},{good},{fail},{category}")
PREDICTIVE_CSV_ROWS = "\n".join(_ROWS)


def _fixture_df() -> pd.DataFrame:
    from io import StringIO
    return pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure,downtime_category\n" + PREDICTIVE_CSV_ROWS + "\n"))


def test_preprocess_returns_engine_ready_result_with_enough_history():
    df = _fixture_df()
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure", "downtime_category_col": "downtime_category",
        },
    }
    result = pm.preprocess(df, config)
    assert result.skip_ml is False
    assert result.task == "classification"
    assert result.target_col == "is_failure_next"
    assert set(result.feature_cols) == {"oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"}
    assert len(result.df) > 0
    assert "grouped" in result.context and "last_x" in result.context


def test_preprocess_flags_insufficient_signal_for_short_history():
    # Only 8 days total - nowhere near build_training_frame's W+H window
    # requirements, so preprocess() must signal skip_ml rather than handing
    # an empty/tiny frame to automl_engine.
    rows = [f"2026-01-{day:02d},M1,480,470,920,900," for day in range(1, 9)]
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
        },
    }
    result = pm.preprocess(df, config)
    assert result.skip_ml is True
    assert any("not enough" in n.lower() or "insufficient" in n.lower() for n in result.notes)
    assert "grouped" in result.context  # postprocess() still needs this for the statistical fallback


def test_preprocess_rejects_missing_columns():
    df = _fixture_df()
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "does_not_exist",
    }}
    with pytest.raises(ValueError):
        pm.preprocess(df, config)
