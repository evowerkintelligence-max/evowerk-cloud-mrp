"""What-if Simulation Engine — ported from the desktop app's backend/simulation_engine.py.

Pure arithmetic, stdlib only. Models manufacturing capacity, cost, OEE, and
throughput under parameter changes, and can auto-derive a starting baseline
from an uploaded dataset's actual rows (OEE-style: planned/operating time,
total/good units) instead of requiring manual data entry.
"""
from __future__ import annotations

import re
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


def run_whatif(scenario_name: str, baseline: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Compute projected manufacturing outcomes after applying `changes` to `baseline`.

    Baseline parameters (all optional, sensible defaults used): shifts_per_day,
    shift_hours, workers_per_shift, avg_wage_per_hour, machine_count,
    throughput_per_shift, oee_pct, demand_units_per_day, defect_rate_pct,
    scrap_cost_per_unit, raw_material_cost_per_unit, selling_price_per_unit.

    Changes accepted (same keys as baseline, plus): demand_change_pct,
    oee_change_pct (percentage points), defect_change_pct (percentage points).
    """
    b_shifts = _safe(baseline.get("shifts_per_day"), 2)
    b_shift_hrs = _safe(baseline.get("shift_hours"), 8)
    b_workers = _safe(baseline.get("workers_per_shift"), 25)
    b_wage = _safe(baseline.get("avg_wage_per_hour"), 20)
    b_machines = _safe(baseline.get("machine_count"), 10)
    b_tput_shift = _safe(baseline.get("throughput_per_shift"), 1000)
    b_oee = _safe(baseline.get("oee_pct"), 75.0)
    b_demand = _safe(baseline.get("demand_units_per_day"), 1800)
    b_defect = _safe(baseline.get("defect_rate_pct"), 3.5)
    b_scrap_cost = _safe(baseline.get("scrap_cost_per_unit"), 5.0)
    b_rm_cost = _safe(baseline.get("raw_material_cost_per_unit"), 15.0)
    b_sell_price = _safe(baseline.get("selling_price_per_unit"), 40.0)

    b_capacity_day = b_tput_shift * b_shifts
    b_good_units = b_capacity_day * (1 - b_defect / 100)
    b_labor_day = b_workers * b_shifts * b_shift_hrs * b_wage
    b_scrap_units = b_capacity_day * (b_defect / 100)
    b_scrap_loss = b_scrap_units * b_scrap_cost
    b_utilisation = (b_demand / b_capacity_day * 100) if b_capacity_day else 0
    b_revenue = min(b_good_units, b_demand) * b_sell_price
    b_rm_cost_day = b_capacity_day * b_rm_cost
    b_margin = b_revenue - b_labor_day - b_rm_cost_day - b_scrap_loss

    p_shifts = _safe(changes.get("shifts_per_day"), b_shifts)
    p_shift_hrs = _safe(changes.get("shift_hours"), b_shift_hrs)
    p_workers = _safe(changes.get("workers_per_shift"), b_workers)
    p_wage = _safe(changes.get("avg_wage_per_hour"), b_wage)
    p_machines = _safe(changes.get("machine_count"), b_machines)
    p_tput_shift = _safe(changes.get("throughput_per_shift"), b_tput_shift)
    p_scrap_cost = _safe(changes.get("scrap_cost_per_unit"), b_scrap_cost)
    p_rm_cost = _safe(changes.get("raw_material_cost_per_unit"), b_rm_cost)
    p_sell_price = _safe(changes.get("selling_price_per_unit"), b_sell_price)

    oee_change = _safe(changes.get("oee_change_pct"), 0)
    p_oee = max(0, min(100, _safe(changes.get("oee_pct"), b_oee) + oee_change))

    oee_factor = (p_oee / b_oee) if b_oee else 1.0
    p_tput_shift = p_tput_shift * oee_factor

    demand_change_pct = _safe(changes.get("demand_change_pct"), 0)
    p_demand = _safe(changes.get("demand_units_per_day"), b_demand * (1 + demand_change_pct / 100))

    defect_change_pp = _safe(changes.get("defect_change_pct"), 0)
    p_defect = max(0, _safe(changes.get("defect_rate_pct"), b_defect) + defect_change_pp)

    p_capacity_day = p_tput_shift * p_shifts
    p_good_units = p_capacity_day * (1 - p_defect / 100)
    p_labor_day = p_workers * p_shifts * p_shift_hrs * p_wage
    p_scrap_units = p_capacity_day * (p_defect / 100)
    p_scrap_loss = p_scrap_units * p_scrap_cost
    p_utilisation = (p_demand / p_capacity_day * 100) if p_capacity_day else 0
    p_revenue = min(p_good_units, p_demand) * p_sell_price
    p_rm_cost_day = p_capacity_day * p_rm_cost
    p_margin = p_revenue - p_labor_day - p_rm_cost_day - p_scrap_loss
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

    def _snap(shifts, shift_hrs, workers, machines, tput_shift, oee, demand, defect, labor, capacity, good, scrap_loss, utilisation, revenue, margin):
        return {
            "shifts_per_day": round(shifts, 1),
            "shift_hours": round(shift_hrs, 1),
            "workers_per_shift": round(workers, 1),
            "machine_count": round(machines, 1),
            "throughput_per_shift": round(tput_shift, 1),
            "daily_capacity": round(capacity, 1),
            "good_units_per_day": round(good, 1),
            "oee_pct": round(oee, 2),
            "demand_units_per_day": round(demand, 1),
            "defect_rate_pct": round(defect, 2),
            "labor_cost_per_day": round(labor, 2),
            "scrap_loss_per_day": round(scrap_loss, 2),
            "utilisation_pct": round(utilisation, 2),
            "daily_revenue": round(revenue, 2),
            "daily_margin": round(margin, 2),
        }

    baseline_snap = _snap(
        b_shifts, b_shift_hrs, b_workers, b_machines, b_tput_shift, b_oee, b_demand, b_defect,
        b_labor_day, b_capacity_day, b_good_units, b_scrap_loss, b_utilisation, b_revenue, b_margin,
    )
    projected_snap = _snap(
        p_shifts, p_shift_hrs, p_workers, p_machines, p_tput_shift, p_oee, p_demand, p_defect,
        p_labor_day, p_capacity_day, p_good_units, p_scrap_loss, p_utilisation, p_revenue, p_margin,
    )
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
        "machine_count": round(machine_count, 0),
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

    derived_fields = ["shifts_per_day", "shift_hours", "machine_count", "throughput_per_shift", "oee_pct", "defect_rate_pct"]

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
