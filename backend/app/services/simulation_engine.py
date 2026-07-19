"""What-if Simulation Engine — ported from the desktop app's backend/simulation_engine.py.

Pure arithmetic, stdlib only. Models manufacturing capacity, cost, OEE, and
throughput under parameter changes, and can auto-derive a starting baseline
from an uploaded dataset's actual rows (OEE-style: planned/operating time,
total/good units) instead of requiring manual data entry.
"""
from __future__ import annotations

import random
import re
import statistics
from typing import Any

_COL_HINTS: dict[str, list[str]] = {
    "planned_time": [r"planned", r"plan_?time", r"scheduled"],
    "operating_time": [r"operating_?time", r"operating", r"run_?time", r"uptime", r"actual_?time"],
    "total_units": [r"total_?unit", r"units_?produced", r"output", r"total_?prod"],
    "good_units": [r"good_?unit", r"pass", r"conform", r"ok_?unit", r"first_?pass"],
    "target_units": [r"target_?unit", r"ideal_?unit", r"planned_?unit"],
    "date": [r"^date$", r"shift_?date", r"period", r"day"],
    "shift": [r"^shift$", r"turn", r"crew"],
    "machine": [r"machine", r"^line$", r"asset", r"equip", r"station"],
}

_DEFAULT_COSTS: dict[str, float] = {
    "avg_wage_per_hour": 20.0,
    "workers_per_shift": 25.0,
    "scrap_cost_per_unit": 5.0,
    "raw_material_cost_per_unit": 15.0,
    "selling_price_per_unit": 40.0,
}


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _day_key(v: Any) -> str:
    """Truncate a date/timestamp cell to its calendar-day portion so distinct-day
    counting isn't inflated by intra-day timestamps."""
    s = str(v or "").strip()
    if not s:
        return s
    s = s.split()[0]
    return s.split("T")[0]


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


def auto_map_columns(headers: list[str]) -> dict[str, str | None]:
    """Best-effort map of semantic role -> actual column name, by naming convention."""
    mapping: dict[str, str | None] = {}
    for role, patterns in _COL_HINTS.items():
        match = None
        for pat in patterns:
            rx = re.compile(pat, re.IGNORECASE)
            match = next((h for h in headers if h and rx.search(str(h))), None)
            if match:
                break
        mapping[role] = match
    return mapping


def derive_baseline_from_rows(
    headers: list[str],
    rows: list[dict[str, Any]],
    mapping: dict[str, str | None] | None = None,
    cost_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn raw OEE-style rows into a run_whatif() baseline + provenance, so the
    what-if simulator opens already knowing the plant instead of requiring
    twelve numbers to be typed in by hand."""
    m = mapping or auto_map_columns(headers)
    cost_overrides = cost_overrides or {}

    c_planned = m.get("planned_time")
    c_oper = m.get("operating_time")
    c_total = m.get("total_units")
    c_good = m.get("good_units")
    c_target = m.get("target_units")
    c_date = m.get("date")
    c_shift = m.get("shift")
    c_machine = m.get("machine")

    missing = [
        k
        for k, v in {"planned_time": c_planned, "operating_time": c_oper, "total_units": c_total, "good_units": c_good}.items()
        if not v
    ]
    if missing:
        raise ValueError(
            "Could not auto-detect required columns: " + ", ".join(missing) + ". Map them manually or rename your columns."
        )

    sum_planned = sum_oper = sum_total = sum_good = sum_target = 0.0
    dates: set = set()
    shifts: set = set()
    machines: set = set()

    for row in rows:
        sum_planned += _safe(row.get(c_planned))
        sum_oper += _safe(row.get(c_oper))
        sum_total += _safe(row.get(c_total))
        sum_good += _safe(row.get(c_good))
        if c_target:
            sum_target += _safe(row.get(c_target))
        if c_date and row.get(c_date):
            dates.add(_day_key(row.get(c_date)))
        if c_shift and row.get(c_shift):
            shifts.add(str(row.get(c_shift)).strip())
        if c_machine and row.get(c_machine):
            machines.add(str(row.get(c_machine)).strip())

    availability = min(1.0, sum_oper / sum_planned) if sum_planned > 0 else 0.0
    quality = min(1.0, sum_good / sum_total) if sum_total > 0 else 0.0

    perf_estimated = True
    performance = 1.0
    if c_target and sum_planned > 0 and sum_target > 0:
        target_rate = sum_target / sum_planned
        ideal_units = sum_oper * target_rate
        if ideal_units > 0:
            performance = min(1.0, sum_total / ideal_units)
            perf_estimated = False

    oee_pct = round(availability * performance * quality * 100, 2)

    period_days = float(len(dates)) if dates else max(1.0, len(rows) / max(1, len(machines) or 1) / max(1, len(shifts) or 3))
    shifts_per_day = float(len(shifts)) if shifts else 2.0
    machine_count = float(len(machines)) if machines else 10.0

    planned_per_row = (sum_planned / len(rows)) if rows else 480.0
    shift_hours = round(planned_per_row / 60.0, 2) if planned_per_row else 8.0

    daily_total_units = (sum_total / period_days) if period_days else sum_total
    throughput_per_shift = (daily_total_units / shifts_per_day) if shifts_per_day else daily_total_units
    daily_good_units = (sum_good / period_days) if period_days else sum_good

    defect_rate_pct = round((1 - quality) * 100, 2)

    baseline: dict[str, Any] = {
        "shifts_per_day": round(shifts_per_day, 1),
        "shift_hours": shift_hours,
        "throughput_per_shift": round(throughput_per_shift, 1),
        "oee_pct": oee_pct,
        "defect_rate_pct": defect_rate_pct,
        "demand_units_per_day": round(daily_good_units, 1),
    }

    assumptions: list[str] = []
    for key, default in _DEFAULT_COSTS.items():
        val = cost_overrides.get(key)
        if val is None:
            baseline[key] = default
            assumptions.append(key)
        else:
            baseline[key] = _safe(val, default)

    if perf_estimated:
        assumptions.append("performance (no target-rate column found — OEE assumes 100% performance)")
    assumptions.append("demand_units_per_day (assumed equal to current good output)")

    derived_fields = ["shifts_per_day", "shift_hours", "throughput_per_shift", "oee_pct", "defect_rate_pct"]

    provenance = {
        "rows": len(rows),
        "period_days": round(period_days, 1),
        "shifts_detected": sorted(shifts),
        "machines": round(machine_count, 0),
        "oee_pct": oee_pct,
        "availability_pct": round(availability * 100, 2),
        "performance_pct": round(performance * 100, 2),
        "quality_pct": round(quality * 100, 2),
        "performance_estimated": perf_estimated,
        "daily_good_units": round(daily_good_units, 1),
    }

    return {
        "baseline": baseline,
        "provenance": provenance,
        "assumptions": assumptions,
        "mapping": m,
        "derived_fields": derived_fields,
    }


DEFAULT_CV: dict[str, float] = {
    "oee_pct": 0.05,
    "defect_rate_pct": 0.15,
    "throughput_per_shift": 0.05,
    "demand_units_per_day": 0.10,
}


def derive_variance_from_rows(
    headers: list[str],
    rows: list[dict[str, Any]],
    mapping: dict[str, str | None] | None = None,
) -> dict[str, float]:
    """Per-metric coefficient of variation (std/mean across calendar days),
    for run_monte_carlo()'s sampling. Groups rows by day (same _day_key/
    auto_map_columns as derive_baseline_from_rows) and computes a daily
    oee_pct/defect_rate_pct/throughput_per_shift series; any metric with
    fewer than 3 distinct days of history falls back to DEFAULT_CV (too
    little data for a meaningful spread). Falls back entirely to DEFAULT_CV
    if the required columns (planned/operating time, total/good units,
    date) aren't present.

    ponytail: daily OEE here is availability x quality only (no performance
    factor - that needs a target-rate column not guaranteed to be mapped).
    This is a variance *estimate*, not the precise OEE baseline value -
    revisit if bands look too tight/loose against real plant data."""
    m = mapping or auto_map_columns(headers)
    c_planned, c_oper = m.get("planned_time"), m.get("operating_time")
    c_total, c_good, c_date = m.get("total_units"), m.get("good_units"), m.get("date")

    if not (c_planned and c_oper and c_total and c_good and c_date):
        return dict(DEFAULT_CV)

    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        day = _day_key(row.get(c_date))
        if not day:
            continue
        d = by_day.setdefault(day, {"planned": 0.0, "oper": 0.0, "total": 0.0, "good": 0.0})
        d["planned"] += _safe(row.get(c_planned))
        d["oper"] += _safe(row.get(c_oper))
        d["total"] += _safe(row.get(c_total))
        d["good"] += _safe(row.get(c_good))

    daily_oee: list[float] = []
    daily_defect: list[float] = []
    daily_tput: list[float] = []
    for d in by_day.values():
        if d["planned"] <= 0 or d["total"] <= 0:
            continue
        availability = min(1.0, d["oper"] / d["planned"])
        quality = min(1.0, d["good"] / d["total"])
        daily_oee.append(availability * quality * 100)
        daily_defect.append((1 - quality) * 100)
        daily_tput.append(d["total"])

    variance = dict(DEFAULT_CV)
    for key, series in (
        ("oee_pct", daily_oee),
        ("defect_rate_pct", daily_defect),
        ("throughput_per_shift", daily_tput),
    ):
        if len(series) >= 3:
            mean = statistics.fmean(series)
            if mean:
                variance[key] = statistics.stdev(series) / mean
    return variance


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
        for key, cv in variance.items():
            if cv <= 0:
                continue
            mean = baseline_snap.get(key, 0.0)
            if mean <= 0:
                continue
            value = random.gauss(mean, cv * mean)
            sampled[key] = max(0.0, min(100.0, value)) if key == "oee_pct" else max(0.0, value)
        projected = _project(sampled, changes)
        for k, v in projected.items():
            results[k].append(v)

    bands: dict[str, dict[str, float]] = {}
    for key, values in results.items():
        qs = statistics.quantiles(values, n=100)
        bands[key] = {"p10": round(qs[9], 1), "p50": round(qs[49], 1), "p90": round(qs[89], 1)}
    return bands
