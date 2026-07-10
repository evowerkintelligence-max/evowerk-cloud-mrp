"""Downtime analysis — ported from the desktop app's routes/downtime.py.

Stateless: total incidents + downtime, MTTR per machine, a Pareto breakdown by
category, and an optional daily trend, aggregated in DuckDB over a dataset.
"""
from __future__ import annotations

from typing import Any

from app.analytics.downtime_calc import dt_grouped, dt_overall
from app.analytics.oee_calc import qi
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def analyze_downtime(dataset: Dataset, req: Any) -> dict:
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)
    unit_factor = 60.0 if req.duration_unit == "hours" else 1.0

    con = ds_service.open_con()
    try:
        ov = dt_overall(con, rel, req.duration_col)
        cat_rows = []
        if req.category_col and req.category_col in headers:
            cat_key = (
                f"COALESCE(NULLIF(TRIM(CAST({qi(req.category_col)} AS VARCHAR)), ''), '(uncategorized)')"
            )
            cat_rows = dt_grouped(con, rel, req.duration_col, cat_key)
        mach_rows = []
        if req.machine_col and req.machine_col in headers:
            mach_key = f"COALESCE(NULLIF(TRIM(CAST({qi(req.machine_col)} AS VARCHAR)), ''), '(unknown)')"
            mach_rows = dt_grouped(con, rel, req.duration_col, mach_key)
        trend_rows = []
        if req.timestamp_col and req.timestamp_col in headers:
            ts_key = f"SUBSTRING(NULLIF(TRIM(CAST({qi(req.timestamp_col)} AS VARCHAR)), ''), 1, 10)"
            trend_rows = dt_grouped(con, rel, req.duration_col, ts_key)
    finally:
        con.close()

    total_incidents = ov["incidents"]
    total_minutes = ov["minutes_raw"] * unit_factor
    mttr = round(total_minutes / total_incidents, 2) if total_incidents > 0 else 0.0

    by_category: list[dict] = []
    cumulative = 0.0
    for cat, acc in sorted(cat_rows, key=lambda kv: kv[1]["minutes_raw"], reverse=True):
        minutes = acc["minutes_raw"] * unit_factor
        pct = round(minutes * 100.0 / total_minutes, 2) if total_minutes > 0 else 0.0
        cumulative = round(cumulative + pct, 2)
        by_category.append({
            "category": cat,
            "incidents": acc["incidents"],
            "total_minutes": round(minutes, 2),
            "pct_of_total": pct,
            "cumulative_pct": min(100.0, cumulative),
        })

    by_machine: list[dict] = []
    for mach, acc in sorted(mach_rows, key=lambda kv: kv[1]["minutes_raw"], reverse=True):
        minutes = acc["minutes_raw"] * unit_factor
        mttr_m = round(minutes / acc["incidents"], 2) if acc["incidents"] > 0 else 0.0
        by_machine.append({
            "machine": mach,
            "incidents": acc["incidents"],
            "total_minutes": round(minutes, 2),
            "mttr_minutes": mttr_m,
        })

    trend: list[dict] = [
        {"date": d, "incidents": acc["incidents"], "total_minutes": round(acc["minutes_raw"] * unit_factor, 2)}
        for d, acc in sorted(trend_rows, key=lambda kv: kv[0])
    ]

    return {
        "total_incidents": total_incidents,
        "total_minutes": round(total_minutes, 2),
        "mttr_minutes": mttr,
        "top_category": (by_category[0]["category"] if by_category else None),
        "invalid_cells": ov["invalid_cells"],
        "rows_with_issues": ov["rows_with_issues"],
        "by_category": by_category,
        "by_machine": by_machine,
        "trend": trend,
    }
