"""OEE compute — ported from the desktop app's routes/oee.py `oee_compute`.

Stateless: aggregates a dataset in DuckDB via the shared oee_calc engine and
returns a plain dict matching OeeComputeResponse (the route wraps it). The math
(availability/performance/quality/OEE, trend, per-machine, revenue, TEEP, Six
Big Losses) is unchanged from the audited desktop implementation.
"""
from __future__ import annotations

from typing import Any, Optional

from app.analytics.oee_calc import (
    compute_components_ideal,
    oee_grouped,
    oee_overall,
    qi,
    rate_sql_expr,
    revenue_loss_ideal,
)
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def _day_key(v: Any) -> str:
    """Truncate a date/timestamp cell to its calendar-day portion."""
    s = str(v or "").strip()
    if not s:
        return s
    for sep in (" ", "T"):
        if sep in s:
            return s.split(sep, 1)[0]
    return s


def _date_span_days(con, rel: str, date_col: str) -> Optional[int]:
    """Inclusive calendar-day span (min → max) of the date column, for TEEP."""
    c = qi(date_col)
    sql = (
        f"SELECT MIN(ts), MAX(ts) FROM (SELECT "  # nosec B608 - date_col is qi()-quoted and validate_columns-checked upstream
        f"COALESCE(TRY_CAST(CAST({c} AS VARCHAR) AS TIMESTAMP), "
        f"CAST(TRY_CAST(CAST({c} AS VARCHAR) AS DATE) AS TIMESTAMP)) AS ts "
        f"FROM {rel})"
    )
    try:
        row = con.execute(sql).fetchone() or (None, None)
    except Exception:
        return None
    lo, hi = row[0], row[1]
    if lo is None or hi is None:
        return None
    lo_d = lo.date() if hasattr(lo, "date") and callable(getattr(lo, "date")) else lo
    hi_d = hi.date() if hasattr(hi, "date") and callable(getattr(hi, "date")) else hi
    try:
        return max(1, int((hi_d - lo_d).days) + 1)
    except Exception:
        return None


def _six_big_losses(
    totals: dict,
    ideal: float,
    fully_estimated: bool,
    sums: dict,
    split_cols: dict,
    unit_price: Optional[float],
) -> Optional[dict]:
    """TPM Six Big Losses decomposition, in minutes (and $). Splits within a
    family are only made when the dataset has the split column mapped."""
    planned, operating = totals["planned"], totals["operating"]
    total, good = totals["total"], totals["good"]
    if planned <= 0:
        return None

    rate_eff = (ideal / operating) if operating > 0 else 0.0
    price = unit_price if (unit_price and unit_price > 0) else None

    avail_min = max(0.0, planned - operating)
    if fully_estimated or ideal <= 0:
        perf_min = 0.0
    else:
        perf_frac = max(0.0, min(1.0, total / ideal))
        perf_min = operating * (1.0 - perf_frac)
    bad_units = max(0.0, total - good)
    qual_min = (bad_units / rate_eff) if rate_eff > 0 else 0.0

    buckets: list[dict] = []

    def add(key: str, label: str, family: str, minutes: float, units: Optional[float] = None):
        cost = None
        if price is not None:
            cost = round(
                (units * price) if family == "quality" and units is not None
                else minutes * rate_eff * price,
                2,
            )
        buckets.append({
            "key": key, "label": label, "family": family, "minutes": round(minutes, 1),
            "units": (round(units, 1) if units is not None else None), "cost": cost,
            "pct_of_loss": 0.0,
        })

    has_up, has_pl = "unplanned_dt" in split_cols, "planned_dt" in split_cols
    availability_split = has_up or has_pl
    if availability_split:
        ef = min(float(sums.get("unplanned_dt", 0.0)), avail_min) if has_up else 0.0
        su = min(float(sums.get("planned_dt", 0.0)), avail_min - ef) if has_pl else 0.0
        rest = max(0.0, avail_min - ef - su)
        if has_up:
            add("equipment_failure", "Equipment Failure (unplanned stops)", "availability", ef)
        if has_pl:
            add("setup_adjustments", "Setup & Adjustments (changeovers)", "availability", su)
        if rest > 0.05:
            add("unclassified_downtime", "Unclassified Downtime", "availability", rest)
    else:
        add("availability_loss", "All Stops (breakdowns + changeovers)", "availability", avail_min)

    performance_split = ("minor_stops" in split_cols) and not fully_estimated
    if fully_estimated:
        add("performance_loss", "Speed & Minor-Stop Losses", "performance", 0.0)
    elif performance_split:
        ms = min(float(sums.get("minor_stops", 0.0)), perf_min)
        add("minor_stops", "Idling & Minor Stops", "performance", ms)
        add("reduced_speed", "Reduced Speed (slow cycles)", "performance", max(0.0, perf_min - ms))
    else:
        add("performance_loss", "Speed & Minor-Stop Losses (combined)", "performance", perf_min)

    quality_split = "startup_rejects" in split_cols
    if quality_split:
        sr = min(float(sums.get("startup_rejects", 0.0)), bad_units)
        sr_min = (sr / rate_eff) if rate_eff > 0 else 0.0
        add("startup_rejects", "Startup Rejects (warm-up scrap)", "quality", sr_min, units=sr)
        add("process_defects", "Process Defects (production rejects)", "quality",
            max(0.0, qual_min - sr_min), units=max(0.0, bad_units - sr))
    else:
        add("quality_loss", "All Defects (startup + production)", "quality", qual_min, units=bad_units)

    total_loss = sum(b["minutes"] for b in buckets)
    for b in buckets:
        b["pct_of_loss"] = round(b["minutes"] / total_loss * 100, 1) if total_loss > 0 else 0.0

    return {
        "total_loss_minutes": round(total_loss, 1),
        "buckets": buckets,
        "availability_split": availability_split,
        "performance_split": performance_split,
        "quality_split": quality_split,
        "performance_estimated": fully_estimated,
    }


def compute_oee(dataset: Dataset, req: Any) -> dict:
    """Run the full OEE computation for a dataset + column mapping. `req` is the
    validated OeeComputeRequest (duck-typed here to avoid a route import cycle)."""
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)

    pc, oc, tc, gc = (
        req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col,
    )
    machine_col = req.machine_col if (req.machine_col and req.machine_col in headers) else None
    rate_sql = rate_sql_expr(machine_col, req.machine_rates, req.target_rate)
    fully_estimated = rate_sql is None

    split_cols = {
        key: col for key, col in [
            ("unplanned_dt", req.unplanned_downtime_col),
            ("planned_dt", req.planned_downtime_col),
            ("minor_stops", req.minor_stops_col),
            ("startup_rejects", req.startup_rejects_col),
        ] if col and col in headers
    }

    con = ds_service.open_con()
    try:
        ov = oee_overall(con, rel, pc, oc, tc, gc, ideal_sql=rate_sql, extra_sum_cols=split_cols)
        trend_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, req.date_col, ideal_sql=rate_sql)
            if (req.date_col and req.date_col in headers) else []
        )
        machine_rows = (
            oee_grouped(con, rel, pc, oc, tc, gc, machine_col, ideal_sql=rate_sql)
            if machine_col else []
        )
        date_span = (
            _date_span_days(con, rel, req.date_col)
            if (req.date_col and req.date_col in headers) else None
        )
    finally:
        con.close()

    totals = {"planned": ov["planned"], "operating": ov["operating"], "total": ov["total"], "good": ov["good"]}
    ideal_total = ov["ideal"]
    unrated_rows = 0 if fully_estimated else ov["unrated_rows"]

    avail, perf, qual, oee, estimated = compute_components_ideal(
        totals["planned"], totals["operating"], totals["total"], totals["good"],
        ideal_total, fully_estimated,
    )

    trend: list[dict] = []
    for date_key, g in sorted(trend_rows, key=lambda kv: kv[0]):
        a, p, q, o, _ = compute_components_ideal(
            g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
        )
        trend.append({
            "date": date_key, "availability": a, "performance": p, "quality": q, "oee": o,
            "total_units": g["total"],
            "invalid_cells": int(g.get("invalid_cells", 0)), "blank_cells": int(g.get("blank_cells", 0)),
        })

    machine_calendar_min = (date_span * 1440.0) if date_span else None

    machines: list[dict] = []
    for mach, g in sorted(machine_rows, key=lambda kv: kv[0]):
        a, p, q, o, _ = compute_components_ideal(
            g["planned"], g["operating"], g["total"], g["good"], g["ideal"], fully_estimated
        )
        m_rev_lost = None
        if req.unit_price and req.unit_price > 0:
            al, pl, ql = revenue_loss_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], req.unit_price)
            m_rev_lost = round(al + pl + ql, 2)
        m_rate = (req.machine_rates or {}).get(mach)
        rate_used = m_rate if (m_rate and m_rate > 0) else (req.target_rate if (req.target_rate and req.target_rate > 0) else None)
        m_util = m_teep = None
        if machine_calendar_min and machine_calendar_min > 0:
            m_util = round(max(0.0, min(1.0, g["planned"] / machine_calendar_min)) * 100, 2)
            m_teep = round(o * m_util / 100.0, 2)
        machines.append({
            "machine": mach, "availability": a, "performance": p, "quality": q, "oee": o,
            "total_units": g["total"], "good_units": g["good"],
            "total_planned": g["planned"], "total_operating": g["operating"],
            "revenue_lost": m_rev_lost,
            "invalid_cells": int(g.get("invalid_cells", 0)), "blank_cells": int(g.get("blank_cells", 0)),
            "rate_used": rate_used, "utilization": m_util, "teep": m_teep,
        })

    teep_result: Optional[dict] = None
    if date_span and date_span > 0 and totals["planned"] > 0:
        machine_count = max(1, len(machine_rows)) if machine_col else 1
        calendar_min = date_span * 1440.0 * machine_count
        utilization_frac = max(0.0, min(1.0, totals["planned"] / calendar_min))
        teep_result = {
            "teep": round((oee / 100.0) * utilization_frac * 100, 2),
            "utilization": round(utilization_frac * 100, 2),
            "calendar_days": date_span,
            "calendar_minutes": round(calendar_min, 1),
            "machine_count": machine_count,
            "machine_count_assumed": not machine_col,
        }

    six_big_losses = _six_big_losses(totals, ideal_total, fully_estimated, ov, split_cols, req.unit_price)

    revenue_impact: Optional[dict] = None
    if req.unit_price and req.unit_price > 0:
        al, pl, ql = revenue_loss_ideal(
            totals["planned"], totals["operating"], totals["total"], totals["good"],
            ideal_total, req.unit_price,
        )
        total_lost = al + pl + ql

        distinct_days = {_day_key(k) for k, _ in trend_rows if _day_key(k)}
        if distinct_days:
            period_days = float(len(distinct_days))
            period_days_estimated = False
        else:
            period_days = max(1.0, ov["row_count"] / 3.0)
            period_days_estimated = True

        daily_rate = round(total_lost / period_days, 2)
        monthly_rate = round(daily_rate * 30, 2)

        current_oee_frac = oee / 100.0
        target_oee_frac = 0.85
        if current_oee_frac < target_oee_frac and totals["planned"] > 0:
            rate_used = (ideal_total / totals["operating"]) if totals["operating"] > 0 else 0
            potential_good_at_target = totals["planned"] * rate_used * target_oee_frac
            benchmark_saving = max(0.0, (potential_good_at_target - totals["good"]) * req.unit_price)
        else:
            benchmark_saving = 0.0

        per_machine_impact = []
        for mach, g in sorted(machine_rows, key=lambda kv: kv[0]):
            mal, mpl, mql = revenue_loss_ideal(g["planned"], g["operating"], g["total"], g["good"], g["ideal"], req.unit_price)
            per_machine_impact.append({
                "machine": str(mach),
                "total_lost": round(mal + mpl + mql, 2),
                "availability_lost": round(mal, 2),
                "performance_lost": round(mpl, 2),
                "quality_lost": round(mql, 2),
            })
        per_machine_impact.sort(key=lambda x: x["total_lost"], reverse=True)

        revenue_impact = {
            "unit_price": req.unit_price,
            "total_lost": round(total_lost, 2),
            "availability_lost": round(al, 2),
            "performance_lost": round(pl, 2),
            "quality_lost": round(ql, 2),
            "daily_rate": daily_rate,
            "monthly_rate": monthly_rate,
            "benchmark_saving": round(benchmark_saving, 2),
            "period_days": round(period_days, 1),
            "period_days_estimated": period_days_estimated,
            "performance_estimated": estimated,
            "per_machine": per_machine_impact,
        }

    return {
        "availability": avail,
        "performance": perf,
        "quality": qual,
        "oee": oee,
        "performance_estimated": estimated,
        "unrated_rows": unrated_rows,
        "total_planned": totals["planned"],
        "total_operating": totals["operating"],
        "total_units": totals["total"],
        "good_units": totals["good"],
        "row_count": ov["row_count"],
        "invalid_cells": ov["invalid_cells"],
        "blank_cells": ov["blank_cells"],
        "rows_with_issues": ov["rows_with_issues"],
        "trend": trend,
        "machines": machines,
        "revenue_impact": revenue_impact,
        "teep": teep_result,
        "six_big_losses": six_big_losses,
    }
