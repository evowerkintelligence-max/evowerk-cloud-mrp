from __future__ import annotations

import statistics

import pytest

from app.services.simulation_engine import (
    DEFAULT_CV,
    PROJECTED_METRIC_KEYS,
    compare_snapshots,
    derive_variance_from_rows,
    run_monte_carlo,
    run_sensitivity,
    run_whatif,
)


def test_compare_snapshots_diffs_every_shared_metric():
    a = {"daily_capacity": 3000.0, "daily_margin": 14475.0, "only_in_a": 5.0}
    b = {"daily_capacity": 2000.0, "daily_margin": 33650.0, "only_in_b": 9.0}

    delta = compare_snapshots(a, b)

    assert set(delta.keys()) == {"daily_capacity", "daily_margin"}
    assert delta["daily_capacity"] == {"absolute": 1000.0, "pct": 50.0, "direction": "increase"}
    assert delta["daily_margin"]["direction"] == "decrease"
    assert delta["daily_margin"]["absolute"] == round(14475.0 - 33650.0, 3)


def test_compare_snapshots_matches_run_whatif_delta_shape():
    """compare_snapshots must return the same {absolute, pct, direction} shape
    run_whatif() already uses for baseline-vs-projected, so the frontend can
    render both with one table component."""
    baseline = {
        "shifts_per_day": 2, "shift_hours": 8, "workers_per_shift": 25, "avg_wage_per_hour": 20,
        "machine_count": 10, "throughput_per_shift": 1000, "oee_pct": 75.0,
        "demand_units_per_day": 1800, "defect_rate_pct": 3.5,
        "scrap_cost_per_unit": 5.0, "raw_material_cost_per_unit": 15.0, "selling_price_per_unit": 40.0,
    }
    run_a = run_whatif("A", baseline, {"shifts_per_day": 3})
    run_b = run_whatif("B", baseline, {})

    delta = compare_snapshots(run_a["projected"], run_b["projected"])

    assert delta["daily_capacity"] == run_a["delta"]["daily_capacity"]


def test_derive_variance_from_rows_computes_cv_from_daily_spread():
    headers = ["date", "shift", "machine", "planned_time", "operating_time", "total_units", "good_units"]
    rows = [
        {"date": "2026-01-01", "shift": "A", "machine": "M1", "planned_time": 480, "operating_time": 480, "total_units": 1000, "good_units": 900},
        {"date": "2026-01-02", "shift": "A", "machine": "M1", "planned_time": 480, "operating_time": 480, "total_units": 1000, "good_units": 800},
        {"date": "2026-01-03", "shift": "A", "machine": "M1", "planned_time": 480, "operating_time": 480, "total_units": 1200, "good_units": 1200},
    ]

    variance = derive_variance_from_rows(headers, rows)

    daily_oee = [90.0, 80.0, 100.0]
    daily_defect = [10.0, 20.0, 0.0]
    daily_tput = [1000.0, 1000.0, 1200.0]
    assert variance["oee_pct"] == pytest.approx(statistics.stdev(daily_oee) / statistics.fmean(daily_oee))
    assert variance["defect_rate_pct"] == pytest.approx(statistics.stdev(daily_defect) / statistics.fmean(daily_defect))
    assert variance["throughput_per_shift"] == pytest.approx(statistics.stdev(daily_tput) / statistics.fmean(daily_tput))
    # demand_units_per_day isn't derivable from OEE-shaped rows - always default
    assert variance["demand_units_per_day"] == DEFAULT_CV["demand_units_per_day"]


def test_derive_variance_from_rows_falls_back_below_three_days():
    headers = ["date", "planned_time", "operating_time", "total_units", "good_units"]
    rows = [
        {"date": "2026-01-01", "planned_time": 480, "operating_time": 480, "total_units": 1000, "good_units": 900},
        {"date": "2026-01-02", "planned_time": 480, "operating_time": 460, "total_units": 1000, "good_units": 850},
    ]

    assert derive_variance_from_rows(headers, rows) == DEFAULT_CV


def test_derive_variance_from_rows_falls_back_without_date_column():
    headers = ["planned_time", "operating_time", "total_units", "good_units"]
    rows = [{"planned_time": 480, "operating_time": 480, "total_units": 1000, "good_units": 900}]

    assert derive_variance_from_rows(headers, rows) == DEFAULT_CV


_ENGINE_TEST_BASELINE = {
    "shifts_per_day": 2, "shift_hours": 8, "workers_per_shift": 25, "avg_wage_per_hour": 20,
    "throughput_per_shift": 1000, "oee_pct": 75.0, "demand_units_per_day": 1800, "defect_rate_pct": 3.5,
    "scrap_cost_per_unit": 5.0, "raw_material_cost_per_unit": 15.0, "selling_price_per_unit": 40.0,
}


def test_run_monte_carlo_bands_are_ordered_for_every_metric():
    bands = run_monte_carlo(_ENGINE_TEST_BASELINE, {"shifts_per_day": 3}, dict(DEFAULT_CV), trials=500)

    assert set(bands.keys()) == PROJECTED_METRIC_KEYS
    for band in bands.values():
        assert band["p10"] <= band["p50"] <= band["p90"]


def test_run_monte_carlo_zero_variance_collapses_to_deterministic_result():
    changes = {"shifts_per_day": 3}
    zero_variance = {k: 0.0 for k in DEFAULT_CV}

    bands = run_monte_carlo(_ENGINE_TEST_BASELINE, changes, zero_variance, trials=50)
    expected = run_whatif("t", _ENGINE_TEST_BASELINE, changes)["projected"]

    for key, expected_value in expected.items():
        assert bands[key] == {"p10": expected_value, "p50": expected_value, "p90": expected_value}


def test_run_sensitivity_ranks_by_impact_on_target_metric():
    variance = {"oee_pct": 0.01, "defect_rate_pct": 0.01, "throughput_per_shift": 0.01, "demand_units_per_day": 0.50}

    ranked = run_sensitivity(_ENGINE_TEST_BASELINE, {}, variance, target_metric="daily_margin")

    assert len(ranked) == 4
    # demand's CV is 50x the others, so it must dominate daily_margin regardless
    # of the underlying per-unit sensitivity coefficients.
    assert ranked[0]["parameter"] == "demand_units_per_day"
    for i in range(len(ranked) - 1):
        assert ranked[i]["impact"] >= ranked[i + 1]["impact"]


def test_run_sensitivity_skips_zero_variance_parameters():
    ranked = run_sensitivity(_ENGINE_TEST_BASELINE, {}, {k: 0.0 for k in DEFAULT_CV}, target_metric="daily_margin")

    assert ranked == []
