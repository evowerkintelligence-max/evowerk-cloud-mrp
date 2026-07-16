"""Throughput analytics — ported from the desktop app's routes/throughput.py.

Units produced per period, peak/average, optional planned-vs-actual efficiency,
and a machine breakdown. Stateless DuckDB aggregation over a dataset.
"""
from __future__ import annotations

from typing import Any, Optional

from app.analytics.oee_calc import date_bucket_expr, date_bucket_unparsed_expr
from app.analytics.oee_calc import inv_expr as _inv
from app.analytics.oee_calc import val_expr as _val
from app.analytics.oee_calc import qi
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def compute_throughput(dataset: Dataset, req: Any) -> dict:
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)

    pcol = req.planned_units_col if (req.planned_units_col and req.planned_units_col in headers) else None
    inv_terms = _inv(req.units_col) + (f" + {_inv(pcol)}" if pcol else "")
    planned_sel = _val(pcol) if pcol else "0.0"

    # nosec B608 - units_col/date_col/machine_col/planned_units_col are
    # validate_columns-checked by the route before reaching here, and every
    # reference below is qi()/val_expr()-quoted; no free-text SQL entry.
    con = ds_service.open_con()
    try:
        r = con.execute(
            f"SELECT SUM({_val(req.units_col)}), {('SUM(' + _val(pcol) + ')') if pcol else 'NULL'}, "  # nosec B608
            f"SUM({inv_terms}), SUM(CASE WHEN ({inv_terms}) > 0 THEN 1 ELSE 0 END), COUNT(*) FROM {rel}"
        ).fetchone() or (0, None, 0, 0, 0)
        total_units = float(r[0] or 0.0)
        total_planned = float(r[1] or 0.0) if pcol else None
        invalid_cells = int(r[2] or 0)
        rows_with_issues = int(r[3] or 0)
        row_count = int(r[4] or 0)

        period_groups: dict[str, dict[str, float]] = {}
        unparsed_date_rows = 0
        if req.date_col and req.date_col in headers:
            dk = date_bucket_expr(req.date_col)
            unp = date_bucket_unparsed_expr(req.date_col)
            for k, u, pl, n in con.execute(
                f"SELECT {dk}, SUM({_val(req.units_col)}), SUM({planned_sel}), SUM({unp}) FROM {rel} "  # nosec B608
                f"WHERE {dk} IS NOT NULL GROUP BY 1"
            ).fetchall():
                period_groups[str(k)] = {"units": float(u or 0.0), "planned": float(pl or 0.0)}
                unparsed_date_rows += int(n or 0)

        machine_groups: dict[str, float] = {}
        if req.machine_col and req.machine_col in headers:
            mk = f"NULLIF(TRIM(CAST({qi(req.machine_col)} AS VARCHAR)), '')"
            for k, u in con.execute(
                f"SELECT {mk}, SUM({_val(req.units_col)}) FROM {rel} WHERE {mk} IS NOT NULL GROUP BY 1"  # nosec B608
            ).fetchall():
                machine_groups[str(k)] = float(u or 0.0)
    finally:
        con.close()

    trend: list[dict] = []
    for period in sorted(period_groups):
        g = period_groups[period]
        planned_val = g["planned"] if pcol else None
        eff = None
        if planned_val and planned_val > 0:
            eff = round(g["units"] / planned_val * 100, 1)  # uncapped: beating plan is real info
        trend.append({
            "period": period,
            "units": round(g["units"], 2),
            "planned": round(planned_val, 2) if planned_val is not None else None,
            "efficiency_pct": eff,
        })

    period_count = len(trend) if trend else 1
    avg_units = round(total_units / period_count, 2)

    # Auto-baseline: no planned/target column mapped, so there's nothing to
    # compare pace against. Suggest the historical per-period average as a
    # flat target line instead of leaving the chart/gauge blank.
    # ponytail: flat historical average, not a seasonal/weighted forecast —
    # good enough to unblank the target line; revisit if noisy production
    # data makes a flat average a bad baseline.
    is_target_estimated = False
    if not pcol and len(trend) >= 2 and avg_units > 0:
        is_target_estimated = True
        for t in trend:
            t["planned"] = avg_units
            t["efficiency_pct"] = round(t["units"] / avg_units * 100, 1)
        total_planned = round(avg_units * period_count, 2)

    peak_units = 0.0
    peak_period: Optional[str] = None
    if trend:
        peak_pt = max(trend, key=lambda t: t["units"])
        peak_units = peak_pt["units"]
        peak_period = peak_pt["period"]
    else:
        peak_units = round(total_units, 2)

    overall_eff = None
    if total_planned and total_planned > 0:
        overall_eff = round(total_units / total_planned * 100, 1)

    grand = total_units or 1.0
    by_machine = sorted(
        [
            {"machine": m, "total_units": round(u, 2), "pct_of_total": round(u / grand * 100, 1)}
            for m, u in machine_groups.items()
        ],
        key=lambda x: x["total_units"],
        reverse=True,
    )

    return {
        "total_units": round(total_units, 2),
        "period_count": period_count,
        "avg_units_per_period": avg_units,
        "peak_units": peak_units,
        "peak_period": peak_period,
        "total_planned": round(total_planned, 2) if total_planned is not None else None,
        "efficiency_pct": overall_eff,
        "row_count": row_count,
        "invalid_cells": invalid_cells,
        "rows_with_issues": rows_with_issues,
        "unparsed_date_rows": unparsed_date_rows,
        "is_target_estimated": is_target_estimated,
        "trend": trend,
        "by_machine": by_machine,
    }
