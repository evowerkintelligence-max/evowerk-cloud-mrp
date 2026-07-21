"""Shift comparison — ported from the desktop app's routes/shifts.py.

Groups a dataset by shift and computes whichever metric families have their
columns mapped (throughput / scrap / OEE / downtime), then ranks best vs worst
shift on a single primary axis. Stateless DuckDB aggregation.

Also computes, when the optional columns are mapped: a per-machine breakdown
within each shift, a day-bucketed shift-over-shift trend, and an hour-bucketed
pacing series — all reusing the exact same value-expression SQL as the main
per-shift query, just grouped on additional keys.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from typing import Any, Optional

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import date_bucket_expr, date_bucket_hour_expr, date_bucket_unparsed_expr
from app.analytics.oee_calc import num_invalid as _inv
from app.analytics.oee_calc import num_val as _val
from app.analytics.oee_calc import qi
from app.models.dataset import Dataset
from app.models.shift_forecast import ShiftForecastDataset
from app.services import datasets as ds_service


def _derive_shift_metrics(
    b: dict, has_throughput: bool, has_scrap: bool, has_oee: bool, has_downtime: bool, target_rate: Optional[float]
) -> dict:
    """OEE/scrap/downtime figures for one (shift[, machine|day]) bucket's raw
    sums — shared by the per-shift breakdown, the per-shift×machine breakdown,
    and the day-bucketed trend, so this block of derived math lives once."""
    total_units = round(b["units"], 2) if has_throughput else None

    scrap_rate = fpq = total_scrap = None
    if has_scrap and b["total"] > 0:
        total_scrap = round(b["scrap"], 2)
        sr = b["scrap"] / b["total"] * 100
        scrap_rate = round(sr, 2)
        fpq = round(100.0 - sr, 2)

    avail = qual = oee_pct = None
    if has_oee and b["planned"] > 0:
        avail = round(min(1.0, b["operating"] / b["planned"]) * 100, 2)
        qual = round(min(1.0, b["good"] / b["total"]) * 100, 2) if b["total"] > 0 else 0.0
        if target_rate and target_rate > 0 and b["operating"] > 0:
            perf = min(1.0, (b["total"] / b["operating"]) / target_rate)
        else:
            perf = 1.0
        oee_pct = round((avail / 100) * perf * (qual / 100) * 100, 2)

    downtime_min = round(b["downtime"], 1) if has_downtime else None

    return {
        "total_units": total_units,
        "total_scrap": total_scrap,
        "scrap_rate_pct": scrap_rate,
        "fpq_pct": fpq,
        "availability_pct": avail,
        "quality_pct": qual,
        "oee_pct": oee_pct,
        "total_downtime_min": downtime_min,
    }


def _primary_key(has_oee: bool, has_scrap: bool, has_throughput: bool, has_downtime: bool) -> tuple:
    """Which single metric ranks best/worst shift and drives the trend series
    — OEE > FPQ > throughput > downtime, whichever metric family is mapped."""
    if has_oee:
        return "oee_pct", True
    if has_scrap:
        return "fpq_pct", True
    if has_throughput:
        return "total_units", True
    if has_downtime:
        return "total_downtime_min", False
    return None, True


def analyze_shifts(dataset: Dataset, req: Any) -> dict:
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)

    has_throughput = bool(req.units_col and req.units_col in headers)
    total_col = req.total_units_col or req.units_col
    has_total = bool(total_col and total_col in headers)
    has_scrap = bool(req.scrap_col and req.scrap_col in headers and has_total)
    has_oee = bool(
        req.planned_col and req.planned_col in headers
        and req.operating_col and req.operating_col in headers
        and has_total
        and req.good_units_col and req.good_units_col in headers
    )
    has_downtime = bool(req.downtime_col and req.downtime_col in headers)
    has_dt_cat = bool(req.downtime_category_col and req.downtime_category_col in headers and has_downtime)
    has_machine = bool(req.machine_col and req.machine_col in headers)
    has_timestamp = bool(req.timestamp_col and req.timestamp_col in headers)
    unit_factor = 60.0 if req.downtime_unit == "hours" else 1.0

    metrics_available: list[str] = []
    if has_throughput:
        metrics_available.append("throughput")
    if has_scrap:
        metrics_available.append("scrap")
    if has_oee:
        metrics_available.append("oee")
    if has_downtime:
        metrics_available.append("downtime")
    if not metrics_available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No metric columns found. Map at least one metric column "
            "(units, scrap, OEE components, or downtime).",
        )

    shift_key = f"COALESCE(NULLIF(TRIM(CAST({qi(req.shift_col)} AS VARCHAR)), ''), 'Unknown')"
    if has_throughput:
        units_term = _val(req.units_col)
    elif has_total:
        units_term = _val(total_col)
    else:
        units_term = "0.0"

    total_sel = f"SUM({_val(total_col)})" if has_total else "0.0"
    scrap_sel = f"SUM({_val(req.scrap_col)})" if has_scrap else "0.0"
    planned_sel = f"SUM({_val(req.planned_col)})" if has_oee else "0.0"
    oper_sel = f"SUM({_val(req.operating_col)})" if has_oee else "0.0"
    good_sel = f"SUM({_val(req.good_units_col)})" if has_oee else "0.0"
    dt_sel = f"SUM({_val(req.downtime_col)})" if has_downtime else "0.0"

    inv_parts: list[str] = []
    if has_total:
        inv_parts.append(_inv(total_col))
    if has_throughput:
        inv_parts.append(_inv(req.units_col))
    if has_scrap:
        inv_parts.append(_inv(req.scrap_col))
    if has_oee:
        inv_parts += [_inv(req.planned_col), _inv(req.operating_col), _inv(req.good_units_col)]
    if has_downtime:
        inv_parts.append(_inv(req.downtime_col))
    inv_expr = " + ".join(inv_parts) if inv_parts else "0"

    # Shared SELECT list for every grouped bucket query (per-shift, per-shift×
    # machine, per-day×shift trend) — same 8 aggregates, just grouped
    # differently, so the raw-sums shape (and _row_to_bucket below) is uniform.
    select_cols = (
        f"COUNT(*), {total_sel}, SUM({units_term}), {scrap_sel}, "
        f"{planned_sel}, {oper_sel}, {good_sel}, {dt_sel}"
    )

    def _row_to_bucket(row: tuple) -> dict:
        return {
            "rows": float(row[0] or 0.0),
            "total": float(row[1] or 0.0),
            "units": float(row[2] or 0.0),
            "scrap": float(row[3] or 0.0),
            "planned": float(row[4] or 0.0),
            "operating": float(row[5] or 0.0),
            "good": float(row[6] or 0.0),
            "downtime": float(row[7] or 0.0) * unit_factor,
        }

    buckets: dict[str, dict[str, float]] = {}
    cat_buckets: dict[str, dict[str, float]] = defaultdict(dict)
    machine_buckets: dict[tuple[str, str], dict[str, float]] = {}
    trend_buckets: dict[tuple[str, str], dict[str, float]] = {}
    hourly_buckets: dict[tuple[str, str], float] = {}
    unparsed_date_rows = 0

    # nosec B608 - every column term above is built from qi()/num_val-quoted,
    # `in headers`-gated (dataset's own sniffed schema) columns; no free-text SQL.
    con = ds_service.open_con()
    try:
        gr = con.execute(
            f"SELECT SUM({inv_expr}), SUM(CASE WHEN ({inv_expr}) > 0 THEN 1 ELSE 0 END), COUNT(*) FROM {rel}"  # nosec B608
        ).fetchone() or (0, 0, 0)
        invalid_cells = int(gr[0] or 0)
        rows_with_issues = int(gr[1] or 0)
        row_count = int(gr[2] or 0)

        for row in con.execute(
            f"SELECT {shift_key}, {select_cols} FROM {rel} GROUP BY 1"  # nosec B608
        ).fetchall():
            buckets[str(row[0])] = _row_to_bucket(row[1:])

        if has_dt_cat:
            cat_key = f"COALESCE(NULLIF(TRIM(CAST({qi(req.downtime_category_col)} AS VARCHAR)), ''), 'Unknown')"
            dval = _val(req.downtime_col)
            for sh, cat, mins in con.execute(
                f"SELECT {shift_key}, {cat_key}, SUM({dval}) FROM {rel} WHERE {dval} > 0 GROUP BY 1, 2"  # nosec B608
            ).fetchall():
                cat_buckets[str(sh)][str(cat)] = float(mins or 0.0) * unit_factor

        if has_machine:
            mach_key = f"NULLIF(TRIM(CAST({qi(req.machine_col)} AS VARCHAR)), '')"
            for row in con.execute(
                f"SELECT {shift_key}, {mach_key}, {select_cols} FROM {rel} "  # nosec B608
                f"WHERE {mach_key} IS NOT NULL GROUP BY 1, 2"
            ).fetchall():
                machine_buckets[(str(row[0]), str(row[1]))] = _row_to_bucket(row[2:])

        if has_timestamp:
            day_key = date_bucket_expr(req.timestamp_col)
            unp = date_bucket_unparsed_expr(req.timestamp_col)
            for row in con.execute(
                f"SELECT {day_key}, {shift_key}, {select_cols}, SUM({unp}) FROM {rel} "  # nosec B608
                f"WHERE {day_key} IS NOT NULL GROUP BY 1, 2"
            ).fetchall():
                trend_buckets[(str(row[0]), str(row[1]))] = _row_to_bucket(row[2:-1])
                unparsed_date_rows += int(row[-1] or 0)

            hour_key = date_bucket_hour_expr(req.timestamp_col)
            for hr, sh, u in con.execute(
                f"SELECT {hour_key}, {shift_key}, SUM({units_term}) FROM {rel} "  # nosec B608
                f"WHERE {hour_key} IS NOT NULL GROUP BY 1, 2"
            ).fetchall():
                hourly_buckets[(str(hr), str(sh))] = float(u or 0.0)
    finally:
        con.close()

    results: list[dict] = []
    for shift in sorted(buckets):
        b = buckets[shift]
        m = _derive_shift_metrics(b, has_throughput, has_scrap, has_oee, has_downtime, req.target_rate)

        dt_by_cat: list[dict] = []
        downtime_min = m["total_downtime_min"]
        if has_dt_cat and downtime_min and downtime_min > 0:
            for cat, mins in sorted(cat_buckets[shift].items(), key=lambda x: x[1], reverse=True):
                dt_by_cat.append({
                    "category": cat,
                    "total_minutes": round(mins, 1),
                    "pct": round(mins / downtime_min * 100, 1),
                })

        results.append({
            "shift": shift,
            "row_count": int(b["rows"]),
            **m,
            "downtime_by_category": dt_by_cat,
        })

    # Rank best/worst on ONE primary axis (OEE > FPY > throughput > downtime).
    key, higher_is_better = _primary_key(has_oee, has_scrap, has_throughput, has_downtime)

    best = worst = None
    ranked = [(r[key], r["shift"]) for r in results if key and r.get(key) is not None]
    if ranked:
        hi = max(ranked, key=lambda x: x[0])[1]
        lo = min(ranked, key=lambda x: x[0])[1]
        best, worst = (hi, lo) if higher_is_better else (lo, hi)

    by_machine: list[dict] = []
    for (shift, machine), b in sorted(machine_buckets.items()):
        m = _derive_shift_metrics(b, has_throughput, has_scrap, has_oee, has_downtime, req.target_rate)
        by_machine.append({"shift": shift, "machine": machine, **m})

    trend: list[dict] = []
    for (day, shift), b in sorted(trend_buckets.items()):
        m = _derive_shift_metrics(b, has_throughput, has_scrap, has_oee, has_downtime, req.target_rate)
        trend.append({"period": day, "shift": shift, "value": m.get(key) if key else None})

    hourly = [
        {"shift": sh, "hour": hr, "units": round(u, 2)}
        for (hr, sh), u in sorted(hourly_buckets.items())
    ]

    return {
        "shifts": results,
        "best_shift": best,
        "worst_shift": worst,
        "metrics_available": metrics_available,
        "row_count": row_count,
        "invalid_cells": invalid_cells,
        "rows_with_issues": rows_with_issues,
        "unparsed_date_rows": unparsed_date_rows,
        "by_machine": by_machine,
        "trend": trend,
        "hourly": hourly,
        "target_rate": req.target_rate,
    }


_METRIC_LABELS = {
    "oee_pct": "OEE",
    "fpq_pct": "FPQ",
    "total_units": "Units",
    "total_downtime_min": "Downtime (min)",
}


async def prepare_forecast_source(
    db: AsyncSession,
    dataset: Dataset,
    req: Any,
    *,
    shift: str,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    background_tasks: "BackgroundTasks | None" = None,
) -> dict:
    """Materialize one shift's day-bucketed trend (the same series the "Shift
    trend" chart already renders) into a small two-column Dataset an AutoML
    forecasting run can train on - overwriting the same Dataset on repeat
    calls for a given (org, shift), mirroring downtime_tracking._materialize.
    """
    if not req.timestamp_col:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Map a timestamp column to enable forecasting.")

    analysis = analyze_shifts(dataset, req)
    points = sorted(
        (t for t in analysis["trend"] if t["shift"] == shift and t["value"] is not None),
        key=lambda t: t["period"],
    )
    if not points:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"No trend data for shift '{shift}' to forecast from."
        )

    metrics = analysis["metrics_available"]
    key, _ = _primary_key(
        has_oee="oee" in metrics, has_scrap="scrap" in metrics,
        has_throughput="throughput" in metrics, has_downtime="downtime" in metrics,
    )
    metric_label = _METRIC_LABELS.get(key or "", "Value")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["period", "value"])
    for p in points:
        writer.writerow([p["period"], p["value"]])
    csv_bytes = buf.getvalue().encode("utf-8")

    mapping = (
        await db.execute(
            select(ShiftForecastDataset).where(
                ShiftForecastDataset.org_id == org_id, ShiftForecastDataset.shift == shift
            )
        )
    ).scalar_one_or_none()

    if mapping is None:
        forecast_dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=created_by,
            name=f"Shift Forecast Source: {shift}", filename="shift_forecast.csv", data=csv_bytes,
        )
        db.add(ShiftForecastDataset(org_id=org_id, shift=shift, dataset_id=forecast_dataset.id))
        await db.flush()
        dataset_id = forecast_dataset.id
    else:
        forecast_dataset = await ds_service.resolve_dataset(db, org_id, str(mapping.dataset_id))
        await ds_service.overwrite_dataset_file(
            db, forecast_dataset, data=csv_bytes, background_tasks=background_tasks
        )
        dataset_id = forecast_dataset.id

    return {
        "dataset_id": dataset_id,
        "target_column": "value",
        "time_column": "period",
        "metric_label": metric_label,
        "rows": len(points),
    }
