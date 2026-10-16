"""What-if Simulation Engine — ported from the desktop app's backend/simulation_engine.py.

Pure arithmetic, stdlib only. Models manufacturing capacity, cost, OEE, and
throughput under parameter changes, and can auto-derive a starting baseline
from an uploaded dataset's actual rows (OEE-style: planned/operating time,
total/good units) instead of requiring manual data entry.
"""
from __future__ import annotations

import random
import re  # noqa: F401
import statistics
from typing import Any

from app.services.simulation_baseline import (  # noqa: F401
    _COL_HINTS, _DEFAULT_COSTS, DEFAULT_CV, _day_key, _safe, auto_map_columns, derive_baseline_from_rows,
    derive_variance_from_rows,
)


def _delta(baseline: float, projected: float) -> dict[str, Any]:
    diff = projected - baseline
    pct = (diff / baseline * 100) if baseline != 0 else 0.0
    return {
        "absolute": round(diff, 3),
        "pct": round(pct, 2),
        "direction": "increase" if diff > 0 else ("decrease" if diff < 0 else "unchanged"),
    }


def compare_snapshots(snapshot_a: dict[str, Any], snapshot_b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-metric delta of `snapshot_a` relative to `snapshot_b` as the
    baseline (e.g. from two saved SimulationRuns) - reuses the same
    {absolute, pct, direction} shape run_whatif() already returns for
    baseline-vs-projected, so a saved run can be diffed against another
    saved run with no new rendering logic. Note the argument order into
    _delta(): snapshot_b is the baseline, snapshot_a is "projected" -
    compare_snapshots(a, b) reads as "how does a differ from b"."""
    keys = snapshot_a.keys() & snapshot_b.keys()
    return {k: _delta(snapshot_b[k], snapshot_a[k]) for k in sorted(keys)}


def _project(baseline: dict[str, Any], changes: dict[str, Any]) -> dict[str, float]:
    """Apply `changes` on top of `baseline` and return the resulting metric
    snapshot (shifts_per_day, daily_capacity, ..., daily_margin). Calling this
    with changes={} computes the baseline snapshot itself - run_whatif() uses
    both forms so there is one source of truth for the arithmetic, and
    run_monte_carlo()/run_sensitivity() reuse it directly for repeated
    evaluation."""
    b_shifts = _safe(baseline.get("shifts_per_day"), 2)
    b_shift_hrs = _safe(baseline.get("shift_hours"), 8)
    b_workers = _safe(baseline.get("workers_per_shift"), 25)
    b_wage = _safe(baseline.get("avg_wage_per_hour"), 20)
    b_tput_shift = _safe(baseline.get("throughput_per_shift"), 1000)
    b_oee = _safe(baseline.get("oee_pct"), 75.0)
    b_demand = _safe(baseline.get("demand_units_per_day"), 1800)
    b_defect = _safe(baseline.get("defect_rate_pct"), 3.5)
    b_scrap_cost = _safe(baseline.get("scrap_cost_per_unit"), 5.0)
    b_rm_cost = _safe(baseline.get("raw_material_cost_per_unit"), 15.0)
    b_sell_price = _safe(baseline.get("selling_price_per_unit"), 40.0)

    p_shifts = _safe(changes.get("shifts_per_day"), b_shifts)
    p_shift_hrs = _safe(changes.get("shift_hours"), b_shift_hrs)
    p_workers = _safe(changes.get("workers_per_shift"), b_workers)
    p_wage = _safe(changes.get("avg_wage_per_hour"), b_wage)
    p_tput_shift = _safe(changes.get("throughput_per_shift"), b_tput_shift)
    p_scrap_cost = _safe(changes.get("scrap_cost_per_unit"), b_scrap_cost)
    p_rm_cost = _safe(changes.get("raw_material_cost_per_unit"), b_rm_cost)
    p_sell_price = _safe(changes.get("selling_price_per_unit"), b_sell_price)

    if changes:
        # A real scenario change - clamp to a physically valid range exactly as
        # the pre-refactor "projected" pass always did (this matches old
        # run_whatif() behavior byte-for-byte, including when `changes` doesn't
        # touch OEE/defect at all - the old code applied this same unconditional
        # clamp/fallback for any non-empty changes).
        oee_change = _safe(changes.get("oee_change_pct"), 0)
        p_oee = max(0, min(100, _safe(changes.get("oee_pct"), b_oee) + oee_change))
        defect_change_pp = _safe(changes.get("defect_change_pct"), 0)
        p_defect = max(0, _safe(changes.get("defect_rate_pct"), b_defect) + defect_change_pp)
    else:
        # The baseline pass (changes={}) - echo the baseline exactly, unclamped,
        # matching the pre-refactor code's separate, unscaled baseline computation
        # (which never applied OEE-factor scaling or defect clamping at all).
        p_oee = b_oee
        p_defect = b_defect

    oee_factor = (p_oee / b_oee) if b_oee else 1.0
    p_tput_shift = p_tput_shift * oee_factor

    demand_change_pct = _safe(changes.get("demand_change_pct"), 0)
    p_demand = _safe(changes.get("demand_units_per_day"), b_demand * (1 + demand_change_pct / 100))

    p_capacity_day = p_tput_shift * p_shifts
    p_good_units = p_capacity_day * (1 - p_defect / 100)
    p_labor_day = p_workers * p_shifts * p_shift_hrs * p_wage
    p_scrap_units = p_capacity_day * (p_defect / 100)
    p_scrap_loss = p_scrap_units * p_scrap_cost
    p_utilisation = (p_demand / p_capacity_day * 100) if p_capacity_day else 0
    p_revenue = min(p_good_units, p_demand) * p_sell_price
    p_rm_cost_day = p_capacity_day * p_rm_cost
    p_margin = p_revenue - p_labor_day - p_rm_cost_day - p_scrap_loss

    return {
        "shifts_per_day": round(p_shifts, 1),
        "shift_hours": round(p_shift_hrs, 1),
        "workers_per_shift": round(p_workers, 1),
        "throughput_per_shift": round(p_tput_shift, 1),
        "daily_capacity": round(p_capacity_day, 1),
        "good_units_per_day": round(p_good_units, 1),
        "oee_pct": round(p_oee, 2),
        "demand_units_per_day": round(p_demand, 1),
        "defect_rate_pct": round(p_defect, 2),
        "labor_cost_per_day": round(p_labor_day, 2),
        "scrap_loss_per_day": round(p_scrap_loss, 2),
        "utilisation_pct": round(p_utilisation, 2),
        "daily_revenue": round(p_revenue, 2),
        "daily_margin": round(p_margin, 2),
    }


PROJECTED_METRIC_KEYS = frozenset(
    {
        "shifts_per_day", "shift_hours", "workers_per_shift", "throughput_per_shift",
        "daily_capacity", "good_units_per_day", "oee_pct", "demand_units_per_day",
        "defect_rate_pct", "labor_cost_per_day", "scrap_loss_per_day", "utilisation_pct",
        "daily_revenue", "daily_margin",
    }
)


def run_whatif(scenario_name: str, baseline: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Compute projected manufacturing outcomes after applying `changes` to `baseline`.

    Baseline parameters (all optional, sensible defaults used): shifts_per_day,
    shift_hours, workers_per_shift, avg_wage_per_hour, throughput_per_shift,
    oee_pct, demand_units_per_day, defect_rate_pct, scrap_cost_per_unit,
    raw_material_cost_per_unit, selling_price_per_unit.

    Changes accepted (same keys as baseline, plus): demand_change_pct,
    oee_change_pct (percentage points), defect_change_pct (percentage points).
    """
    baseline_snap = _project(baseline, {})
    projected_snap = _project(baseline, changes)

    b_capacity_day = baseline_snap["daily_capacity"]
    p_capacity_day = projected_snap["daily_capacity"]
    b_oee = baseline_snap["oee_pct"]
    p_oee = projected_snap["oee_pct"]
    b_defect = baseline_snap["defect_rate_pct"]
    p_defect = projected_snap["defect_rate_pct"]
    b_scrap_loss = baseline_snap["scrap_loss_per_day"]
    p_scrap_loss = projected_snap["scrap_loss_per_day"]
    b_labor_day = baseline_snap["labor_cost_per_day"]
    p_labor_day = projected_snap["labor_cost_per_day"]
    b_margin = baseline_snap["daily_margin"]
    p_margin = projected_snap["daily_margin"]
    p_demand = projected_snap["demand_units_per_day"]
    p_utilisation = projected_snap["utilisation_pct"]
    p_capacity_gap = p_capacity_day - p_demand

    insights: list[str] = []

    if abs(p_capacity_day - b_capacity_day) > 0.5:
        direction = "increases" if p_capacity_day > b_capacity_day else "decreases"
        insights.append(
            f"Daily capacity {direction} from {b_capacity_day:,.0f} to {p_capacity_day:,.0f} units "
            f"({_delta(b_capacity_day, p_capacity_day)['pct']:+.1f}%)."
        )

    if p_capacity_gap < 0:
        shortfall = abs(p_capacity_gap)
        insights.append(
            f"⚠ Demand shortfall: capacity is {shortfall:,.0f} units/day below projected demand "
            f"({p_demand:,.0f}). Consider additional shifts or overtime."
        )
    elif p_capacity_gap > b_capacity_day * 0.3:
        insights.append(
            f"Surplus capacity of {p_capacity_gap:,.0f} units/day — "
            f"utilisation drops to {p_utilisation:.1f}%. "
            "Consider deferring capital expenditure or expanding to new markets."
        )

    if abs(p_oee - b_oee) > 0.5:
        direction = "improves" if p_oee > b_oee else "declines"
        insights.append(f"OEE {direction} from {b_oee:.1f}% to {p_oee:.1f}%, boosting effective throughput.")

    if abs(p_defect - b_defect) > 0.1:
        direction = "falls" if p_defect < b_defect else "rises"
        saved = b_scrap_loss - p_scrap_loss
        insights.append(
            f"Defect rate {direction} from {b_defect:.1f}% to {p_defect:.1f}%. "
            f"Scrap cost {'saving' if saved > 0 else 'increase'}: ${abs(saved):,.0f}/day."
        )

    if abs(p_labor_day - b_labor_day) > 10:
        direction = "increases" if p_labor_day > b_labor_day else "decreases"
        insights.append(
            f"Daily labour cost {direction} by ${abs(p_labor_day - b_labor_day):,.0f} "
            f"(${b_labor_day:,.0f} → ${p_labor_day:,.0f})."
        )

    margin_delta = p_margin - b_margin
    if abs(margin_delta) > 10:
        direction = "improves" if margin_delta > 0 else "decreases"
        insights.append(
            f"Estimated daily margin {direction} by ${abs(margin_delta):,.0f} "
            f"(${b_margin:,.0f} → ${p_margin:,.0f})."
        )

    if not insights:
        insights.append("No significant changes detected. Adjust parameters to model a different scenario.")

    delta_snap = {k: _delta(baseline_snap[k], projected_snap[k]) for k in baseline_snap}

    return {
        "scenario_name": scenario_name,
        "baseline": baseline_snap,
        "projected": projected_snap,
        "delta": delta_snap,
        "insights": insights,
    }


def run_monte_carlo(
    baseline: dict[str, Any],
    changes: dict[str, Any],
    variance: dict[str, float],
    trials: int = 2000,
) -> dict[str, dict[str, float]]:
    """Sample the varying baseline metrics `trials` times from a clipped
    normal distribution using `variance`'s coefficients of variation, apply
    `changes` deterministically on top via _project(), and return each
    projected metric's P10/P50/P90 across the trials. A metric with CV <= 0
    is held fixed at its baseline value in every trial (this is how
    zero-variance input collapses the bands to a single deterministic
    value, with no sampling noise at all)."""
    baseline_snap = _project(baseline, {})
    results: dict[str, list[float]] = {k: [] for k in PROJECTED_METRIC_KEYS}

    for _ in range(trials):
        sampled = dict(baseline)
        trial_changes = dict(changes)
        for key, cv in variance.items():
            if cv <= 0:
                continue
            mean = baseline_snap.get(key, 0.0)
            if mean <= 0:
                continue
            value = random.gauss(mean, cv * mean)
            if key == "oee_pct":
                # OEE only affects output via _project()'s p_oee/b_oee ratio, which
                # is a no-op unless `changes` carries an explicit OEE delta - mutating
                # the baseline dict's oee_pct (like every other sampled metric) would
                # silently have zero effect. Express the sample as a delta instead.
                value = max(0.0, min(100.0, value))
                base_change = _safe(trial_changes.get("oee_change_pct"), 0)
                trial_changes["oee_change_pct"] = base_change + (value - mean)
            else:
                sampled[key] = max(0.0, value)
        projected = _project(sampled, trial_changes)
        for k, v in projected.items():
            results[k].append(v)

    bands: dict[str, dict[str, float]] = {}
    for key, values in results.items():
        qs = statistics.quantiles(values, n=100)
        bands[key] = {"p10": round(qs[9], 1), "p50": round(qs[49], 1), "p90": round(qs[89], 1)}
    return bands


def run_sensitivity(
    baseline: dict[str, Any],
    changes: dict[str, Any],
    variance: dict[str, float],
    target_metric: str,
) -> list[dict[str, Any]]:
    """Rank baseline parameters by how much swinging them +/-1 standard
    deviation (using `variance`'s coefficients of variation - the same
    'plausible range' run_monte_carlo() samples from) moves `target_metric`,
    holding everything else at baseline and `changes` fixed. Returns the
    top 8 by impact, descending. A parameter with CV <= 0 never appears -
    it has no plausible range to sweep."""
    baseline_snap = _project(baseline, {})
    ranked: list[dict[str, Any]] = []

    for key, cv in variance.items():
        if cv <= 0:
            continue
        mean = baseline_snap.get(key, 0.0)
        if mean <= 0:
            continue
        spread = cv * mean

        if key == "oee_pct":
            # Same reason as run_monte_carlo(): OEE only affects output via a
            # changes-expressed delta, not via the baseline dict directly.
            base_change = _safe(changes.get("oee_change_pct"), 0)
            low_proj = _project(baseline, {**changes, "oee_change_pct": base_change - spread})[target_metric]
            high_proj = _project(baseline, {**changes, "oee_change_pct": base_change + spread})[target_metric]
        else:
            low_input, high_input = dict(baseline), dict(baseline)
            low_input[key] = max(0.0, mean - spread)
            high_input[key] = mean + spread
            low_proj = _project(low_input, changes)[target_metric]
            high_proj = _project(high_input, changes)[target_metric]

        ranked.append(
            {
                "parameter": key,
                "baseline_value": round(mean, 2),
                "low_value": low_proj,
                "high_value": high_proj,
                "impact": round(abs(high_proj - low_proj), 2),
            }
        )

    ranked.sort(key=lambda r: r["impact"], reverse=True)
    return ranked[:8]
