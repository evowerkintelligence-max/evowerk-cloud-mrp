from __future__ import annotations

from streaming.metrics_calc import compute_throughput_rate, compute_window_metrics


def test_throughput_sums_units_col():
    rows = [{"units": 10}, {"units": 15}]
    metrics = compute_window_metrics(rows, {"units_col": "units"})
    assert metrics["throughput"] == 25.0


def test_scrap_rate_and_fpy_from_scrap_and_total_units():
    rows = [{"units": 10, "scrap": 1}, {"units": 10, "scrap": 1}]
    metrics = compute_window_metrics(rows, {"units_col": "units", "scrap_col": "scrap"})
    assert metrics["scrap_rate_pct"] == 10.0
    assert metrics["fpy_pct"] == 90.0


def test_oee_components_from_full_column_map():
    rows = [{"units": 100, "good": 95, "planned_time": 60, "operating_time": 55, "target": 100}]
    metrics = compute_window_metrics(rows, {
        "units_col": "units",
        "good_units_col": "good",
        "planned_time_col": "planned_time",
        "operating_time_col": "operating_time",
        "target_rate_col": "target",
    })
    assert metrics["availability_pct"] == round(55 / 60 * 100, 1)
    assert metrics["quality_pct"] == 95.0
    assert metrics["performance_pct"] == 100.0
    assert metrics["oee_pct"] == round((55 / 60 * 100) * 1.0 * 0.95, 1)


def test_missing_column_map_keys_omit_that_metric():
    assert compute_window_metrics([{"x": 1}], {}) == {}


def test_downtime_col_sums_minutes():
    rows = [{"down": 2.5}, {"down": 1.5}]
    metrics = compute_window_metrics(rows, {"downtime_col": "down"})
    assert metrics["total_downtime_min"] == 4.0


def test_throughput_rate_uses_cumulative_units_over_duration():
    assert compute_throughput_rate(cumulative_units=120.0, duration_s=60) == 120.0
    assert compute_throughput_rate(cumulative_units=0.0, duration_s=0) is None
