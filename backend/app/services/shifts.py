"""Shift comparison — ported from the desktop app's routes/shifts.py.

Groups a dataset by shift and computes whichever metric families have their
columns mapped (throughput / scrap / OEE / downtime), then ranks best vs worst
shift on a single primary axis. Stateless DuckDB aggregation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException, status

from app.analytics.oee_calc import num_invalid as _inv
from app.analytics.oee_calc import num_val as _val
from app.analytics.oee_calc import qi
from app.models.dataset import Dataset
from app.services import datasets as ds_service


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

    buckets: dict[str, dict[str, float]] = {}
    cat_buckets: dict[str, dict[str, float]] = defaultdict(dict)

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
            f"SELECT {shift_key}, COUNT(*), {total_sel}, SUM({units_term}), {scrap_sel}, "  # nosec B608
            f"{planned_sel}, {oper_sel}, {good_sel}, {dt_sel} FROM {rel} GROUP BY 1"
        ).fetchall():
            buckets[str(row[0])] = {
                "rows": float(row[1] or 0.0),
                "total": float(row[2] or 0.0),
                "units": float(row[3] or 0.0),
                "scrap": float(row[4] or 0.0),
                "planned": float(row[5] or 0.0),
                "operating": float(row[6] or 0.0),
                "good": float(row[7] or 0.0),
                "downtime": float(row[8] or 0.0) * unit_factor,
            }

        if has_dt_cat:
            cat_key = f"COALESCE(NULLIF(TRIM(CAST({qi(req.downtime_category_col)} AS VARCHAR)), ''), 'Unknown')"
            dval = _val(req.downtime_col)
            for sh, cat, mins in con.execute(
                f"SELECT {shift_key}, {cat_key}, SUM({dval}) FROM {rel} WHERE {dval} > 0 GROUP BY 1, 2"  # nosec B608
            ).fetchall():
                cat_buckets[str(sh)][str(cat)] = float(mins or 0.0) * unit_factor
    finally:
        con.close()

    results: list[dict] = []
    for shift in sorted(buckets):
        b = buckets[shift]
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
            if req.target_rate and req.target_rate > 0 and b["operating"] > 0:
                perf = min(1.0, (b["total"] / b["operating"]) / req.target_rate)
            else:
                perf = 1.0
            oee_pct = round((avail / 100) * perf * (qual / 100) * 100, 2)

        downtime_min = round(b["downtime"], 1) if has_downtime else None

        dt_by_cat: list[dict] = []
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
            "total_units": total_units,
            "total_scrap": total_scrap,
            "scrap_rate_pct": scrap_rate,
            "fpq_pct": fpq,
            "availability_pct": avail,
            "quality_pct": qual,
            "oee_pct": oee_pct,
            "total_downtime_min": downtime_min,
            "downtime_by_category": dt_by_cat,
        })

    # Rank best/worst on ONE primary axis (OEE > FPY > throughput > downtime).
    if has_oee:
        key, higher_is_better = "oee_pct", True
    elif has_scrap:
        key, higher_is_better = "fpq_pct", True
    elif has_throughput:
        key, higher_is_better = "total_units", True
    elif has_downtime:
        key, higher_is_better = "total_downtime_min", False
    else:
        key, higher_is_better = None, True

    best = worst = None
    ranked = [(r[key], r["shift"]) for r in results if key and r.get(key) is not None]
    if ranked:
        hi = max(ranked, key=lambda x: x[0])[1]
        lo = min(ranked, key=lambda x: x[0])[1]
        best, worst = (hi, lo) if higher_is_better else (lo, hi)

    return {
        "shifts": results,
        "best_shift": best,
        "worst_shift": worst,
        "metrics_available": metrics_available,
        "row_count": row_count,
        "invalid_cells": invalid_cells,
        "rows_with_issues": rows_with_issues,
    }
