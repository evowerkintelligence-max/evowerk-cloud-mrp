from __future__ import annotations

from app.services.simulation_engine import compare_snapshots, run_whatif


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
