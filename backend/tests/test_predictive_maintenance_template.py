from __future__ import annotations

import pandas as pd
import pytest

from app.services import automl_engine
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


def test_postprocess_with_trained_model_produces_predictive_result():
    df = _fixture_df()
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure", "downtime_category_col": "downtime_category",
        },
        "hourly_value": 1000.0,
    }
    pre = pm.preprocess(df, config)
    assert pre.skip_ml is False

    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, algorithms=pre.algorithms, thoroughness="fast",
    )

    out = pm.postprocess(engine_result, config, pre.context)
    result = out["predictive_result"]
    machines = {m["machine"]: m for m in result["machines"]}
    assert "M1" in machines and "M2" in machines
    assert all(0.0 <= m["failure_probability"] <= 1.0 for m in machines.values())
    assert all(m["ml_powered"] is True for m in machines.values())
    assert result["model_meta"] is not None
    assert result["model_meta"]["algorithm"] in pm._SAFE_ALGORITHMS
    assert out["pipeline"] is not None
    assert out["explain_X"] is not None
    assert result["cost_summary"]["machines_at_risk"] >= 0


def test_postprocess_without_trained_model_falls_back_to_statistical():
    rows = [f"2026-01-{day:02d},M1,480,{150 if day > 5 else 470},{200 if day > 5 else 920},{150 if day > 5 else 900}," for day in range(1, 9)]
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
    }, "hourly_value": 1000.0}

    pre = pm.preprocess(df, config)
    assert pre.skip_ml is True

    out = pm.postprocess(None, config, pre.context)
    result = out["predictive_result"]
    assert len(result["machines"]) == 1
    assert result["machines"][0]["ml_powered"] is False
    assert result["model_meta"] is None
    assert out["pipeline"] is None
    assert out["explain_X"] is None


def test_fleet_oee_median_is_shared_across_machines_not_per_machine(monkeypatch):
    # 3 machines, all too short for ML (< _W+_H+1 rows each), so all take the
    # statistical fallback. Each machine's own OEE history differs sharply
    # (M1 healthy, M2 degrading, M3 crashed), so if fleet_oee_median were
    # computed per-machine from its own history (the bug) instead of once
    # from the fleet's latest-OEE values, the three calls below would see
    # different fleet_oee_median values instead of one shared value.
    rows = []
    for day in range(1, 9):
        rows.append(f"2026-01-{day:02d},M1,480,470,920,900,")
        rows.append(f"2026-01-{day:02d},M2,480,{460 - day * 20},{900 - day * 20},{880 - day * 20},")
        rows.append(f"2026-01-{day:02d},M3,480,100,200,150,")
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
    }, "hourly_value": 1000.0}

    pre = pm.preprocess(df, config)
    assert pre.skip_ml is True

    seen_medians: list[float] = []
    real_stat_analyze = pm.legacy._stat_analyze

    def _spy(oee_s, avail_s, perf_s, qual_s, last_fail_days, fleet_oee_median):
        seen_medians.append(fleet_oee_median)
        return real_stat_analyze(oee_s, avail_s, perf_s, qual_s, last_fail_days, fleet_oee_median)

    monkeypatch.setattr(pm.legacy, "_stat_analyze", _spy)
    out = pm.postprocess(None, config, pre.context)
    assert len(out["predictive_result"]["machines"]) == 3
    assert len(seen_medians) == 3
    assert len(set(seen_medians)) == 1, f"fleet_oee_median must be the same single value for every machine, got {seen_medians}"


def test_last_failure_days_ago_uses_failure_col_when_downtime_category_unmapped():
    # failure_col mapped, downtime_category_col NOT mapped - a legitimate
    # legacy-supported configuration. daily_ml["is_failure"] is exclusively
    # downtime_category_col-derived (Task 4 behavior, must stay that way), so
    # it's all zero here; last_failure_days_ago must come from context["df"]'s
    # raw failure_col instead, matching legacy analyze()'s preference order.
    rows = [f"2026-01-{day:02d},M1,480,470,920,900,{'1' if day == 3 else ''}" for day in range(1, 9)]
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
        "failure_col": "failure",
    }, "hourly_value": 1000.0}

    pre = pm.preprocess(df, config)
    assert pre.skip_ml is True
    assert (pre.context["daily_ml"]["is_failure"] == 0).all()

    out = pm.postprocess(None, config, pre.context)
    machine = out["predictive_result"]["machines"][0]
    assert machine["last_failure_days_ago"] == 5.0  # day 8 (last row) - day 3 (failure) = 5 days


def test_preprocess_rejects_missing_columns():
    df = _fixture_df()
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "does_not_exist",
    }}
    with pytest.raises(ValueError):
        pm.preprocess(df, config)
