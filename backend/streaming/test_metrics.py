# backend/streaming/test_metrics.py
from __future__ import annotations

from streaming.metrics import compute_window_metrics


def test_throughput_and_scrap_rate():
    rows = [
        {"units": 10, "scrap": 1},
        {"units": 20, "scrap": 2},
    ]
    column_map = {"units_col": "units", "scrap_col": "scrap", "total_units_col": "units"}
    result = compute_window_metrics(rows, column_map)
    assert result["throughput"] == 30.0
    assert result["scrap_rate_pct"] == 10.0
    assert result["fpy_pct"] == 90.0


def test_oee_components():
    rows = [
        {"planned": 60, "operating": 55, "good": 90, "units": 100, "target": 100},
    ]
    column_map = {
        "planned_time_col": "planned", "operating_time_col": "operating",
        "good_units_col": "good", "units_col": "units", "target_rate_col": "target",
    }
    result = compute_window_metrics(rows, column_map)
    assert result["availability_pct"] == round(55 / 60 * 100, 1)
    assert result["quality_pct"] == 90.0
    assert result["performance_pct"] == 100.0
    assert result["oee_pct"] == round((55 / 60 * 100) * 1.0 * 0.9, 1)


def test_empty_window_returns_no_metrics_keys_that_need_division():
    result = compute_window_metrics([], {"units_col": "units"})
    assert result["throughput"] == 0.0
    assert "throughput_rate" not in result  # age_min not meaningful without a StreamRunner-style clock; producer supplies duration separately


def test_downtime_column():
    rows = [{"downtime": 5}, {"downtime": 3}]
    result = compute_window_metrics(rows, {"downtime_col": "downtime"})
    assert result["total_downtime_min"] == 8.0
