"""Baseline + variance derivation for the what-if simulation engine: maps an
uploaded dataset's columns to semantic roles by naming convention and derives
a starting baseline / per-metric variance from its actual rows. Split out of
simulation_engine.py, which re-exports every name here. Pure, stdlib only."""
from __future__ import annotations

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
