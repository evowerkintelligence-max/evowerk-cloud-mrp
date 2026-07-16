"""Scrap / quality-yield analytics — ported from the desktop app's routes/scrap.py.

Overall scrap rate, First Pass Yield, defects-per-1000, a Pareto by rejection
reason, and machine/period breakdowns. Stateless DuckDB aggregation.
"""
from __future__ import annotations

from typing import Any

from app.analytics.oee_calc import inv_expr, qi, val_expr
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def compute_scrap(dataset: Dataset, req: Any) -> dict:
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)
    # val_expr floors physically-impossible negative unit/scrap counts to 0 and
    # inv_expr counts them as data issues, matching oee_calc's convention.
    tval, sval = val_expr(req.total_units_col), val_expr(req.scrap_col)
    inv_terms = f"{inv_expr(req.total_units_col)} + {inv_expr(req.scrap_col)}"

    # nosec B608 - every column below is gated by an `in headers` (dataset's own
    # sniffed schema) check and qi()/val_expr-quoted before interpolation.
    con = ds_service.open_con()
    try:
        r = con.execute(
            f"SELECT SUM({tval}), SUM({sval}), SUM({inv_terms}), "  # nosec B608
            f"SUM(CASE WHEN ({inv_terms}) > 0 THEN 1 ELSE 0 END), COUNT(*) FROM {rel}"
        ).fetchone() or (0, 0, 0, 0, 0)
        total_units = float(r[0] or 0.0)
        total_scrap = float(r[1] or 0.0)
        invalid_cells = int(r[2] or 0)
        rows_with_issues = int(r[3] or 0)
        row_count = int(r[4] or 0)

        reason_groups: dict[str, dict[str, float]] = {}
        if req.reason_col and req.reason_col in headers:
            rk = f"COALESCE(NULLIF(TRIM(CAST({qi(req.reason_col)} AS VARCHAR)), ''), 'Unknown')"
            for k, s, inv in con.execute(
                f"SELECT {rk}, SUM({sval}), SUM({inv_expr(req.scrap_col)}) FROM {rel} GROUP BY 1"  # nosec B608
            ).fetchall():
                reason_groups[str(k)] = {"scrap": float(s or 0.0), "invalid_cells": float(inv or 0)}

        machine_groups: dict[str, dict[str, float]] = {}
        if req.machine_col and req.machine_col in headers:
            mk = f"NULLIF(TRIM(CAST({qi(req.machine_col)} AS VARCHAR)), '')"
            for k, t, s, inv in con.execute(
                f"SELECT {mk}, SUM({tval}), SUM({sval}), SUM({inv_terms}) FROM {rel} "  # nosec B608
                f"WHERE {mk} IS NOT NULL GROUP BY 1"
            ).fetchall():
                machine_groups[str(k)] = {"total": float(t or 0.0), "scrap": float(s or 0.0), "invalid_cells": float(inv or 0)}

        period_groups: dict[str, dict[str, float]] = {}
        if req.date_col and req.date_col in headers:
            dk = f"SUBSTRING(NULLIF(TRIM(CAST({qi(req.date_col)} AS VARCHAR)), ''), 1, 10)"
            for k, t, s, inv in con.execute(
                f"SELECT {dk}, SUM({tval}), SUM({sval}), SUM({inv_terms}) FROM {rel} "  # nosec B608
                f"WHERE {dk} IS NOT NULL GROUP BY 1"
            ).fetchall():
                period_groups[str(k)] = {"total": float(t or 0.0), "scrap": float(s or 0.0), "invalid_cells": float(inv or 0)}
    finally:
        con.close()

    scrap_rate = round(total_scrap / total_units * 100, 2) if total_units > 0 else 0.0
    fpq = round(100.0 - scrap_rate, 2)
    defect_per_1000 = round(total_scrap / total_units * 1000, 2) if total_units > 0 else 0.0

    by_reason: list[dict] = []
    if reason_groups:
        sorted_reasons = sorted(reason_groups.items(), key=lambda x: x[1]["scrap"], reverse=True)
        grand_scrap = sum(g["scrap"] for _, g in sorted_reasons) or 1.0
        cumulative = 0.0
        for reason, g in sorted_reasons:
            pct = round(g["scrap"] / grand_scrap * 100, 1)
            cumulative += pct
            by_reason.append({
                "reason": reason,
                "count": round(g["scrap"], 2),
                "pct_of_total": pct,
                "cumulative_pct": round(min(cumulative, 100.0), 1),
                "invalid_cells": int(g["invalid_cells"]),
            })

    by_machine = sorted(
        [
            {
                "machine": m,
                "total_units": round(g["total"], 2),
                "scrap_units": round(g["scrap"], 2),
                "scrap_rate_pct": round(g["scrap"] / g["total"] * 100, 2) if g["total"] > 0 else 0.0,
                "invalid_cells": int(g["invalid_cells"]),
            }
            for m, g in machine_groups.items()
        ],
        key=lambda x: x["scrap_rate_pct"],
        reverse=True,
    )

    trend = sorted(
        [
            {
                "period": k,
                "total_units": round(g["total"], 2),
                "scrap_units": round(g["scrap"], 2),
                "scrap_rate_pct": round(g["scrap"] / g["total"] * 100, 2) if g["total"] > 0 else 0.0,
                "invalid_cells": int(g["invalid_cells"]),
            }
            for k, g in period_groups.items()
        ],
        key=lambda x: x["period"],
    )

    return {
        "total_units": round(total_units, 2),
        "total_scrap": round(total_scrap, 2),
        "scrap_rate_pct": scrap_rate,
        "fpq_pct": fpq,
        "defect_rate_per_1000": defect_per_1000,
        "row_count": row_count,
        "invalid_cells": invalid_cells,
        "rows_with_issues": rows_with_issues,
        "by_reason": by_reason,
        "by_machine": by_machine,
        "trend": trend,
    }
