"""Scrap / quality-yield analytics — ported from the desktop app's routes/scrap.py.

Overall scrap rate, First Pass Yield, defects-per-1000, a Pareto by rejection
reason, and machine/period breakdowns. Stateless DuckDB aggregation.
"""
from __future__ import annotations

from typing import Any

from app.analytics.oee_calc import date_bucket_expr, num_val, qi
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def compute_scrap(dataset: Dataset, req: Any) -> dict:
    headers = {c["name"] for c in dataset.columns}
    rel = ds_service.dataset_relation(dataset)
    tval, sval = num_val(req.total_units_col), num_val(req.scrap_col)

    # nosec B608 - every column below is gated by an `in headers` (dataset's own
    # sniffed schema) check and qi()/num_val-quoted before interpolation.
    con = ds_service.open_con()
    try:
        r = con.execute(f"SELECT SUM({tval}), SUM({sval}), COUNT(*) FROM {rel}").fetchone() or (0, 0, 0)  # nosec B608
        total_units = float(r[0] or 0.0)
        total_scrap = float(r[1] or 0.0)
        row_count = int(r[2] or 0)

        reason_groups: dict[str, float] = {}
        if req.reason_col and req.reason_col in headers:
            rk = f"COALESCE(NULLIF(TRIM(CAST({qi(req.reason_col)} AS VARCHAR)), ''), 'Unknown')"
            for k, s in con.execute(f"SELECT {rk}, SUM({sval}) FROM {rel} GROUP BY 1").fetchall():  # nosec B608
                reason_groups[str(k)] = float(s or 0.0)

        machine_groups: dict[str, dict[str, float]] = {}
        if req.machine_col and req.machine_col in headers:
            mk = f"NULLIF(TRIM(CAST({qi(req.machine_col)} AS VARCHAR)), '')"
            for k, t, s in con.execute(
                f"SELECT {mk}, SUM({tval}), SUM({sval}) FROM {rel} WHERE {mk} IS NOT NULL GROUP BY 1"  # nosec B608
            ).fetchall():
                machine_groups[str(k)] = {"total": float(t or 0.0), "scrap": float(s or 0.0)}

        period_groups: dict[str, dict[str, float]] = {}
        if req.date_col and req.date_col in headers:
            dk = date_bucket_expr(req.date_col)
            for k, t, s in con.execute(
                f"SELECT {dk}, SUM({tval}), SUM({sval}) FROM {rel} WHERE {dk} IS NOT NULL GROUP BY 1"  # nosec B608
            ).fetchall():
                period_groups[str(k)] = {"total": float(t or 0.0), "scrap": float(s or 0.0)}
    finally:
        con.close()

    scrap_rate = round(total_scrap / total_units * 100, 2) if total_units > 0 else 0.0
    fpq = round(100.0 - scrap_rate, 2)
    defect_per_1000 = round(total_scrap / total_units * 1000, 2) if total_units > 0 else 0.0

    # Wrong column mappings produce plausible-looking garbage; flag the two
    # cases that can only mean a mismapped column so the UI can warn.
    warnings: list[str] = []
    # total_scrap > 0 guard keeps all-zero datasets warning-free; comparing
    # against total_units (even 0, e.g. a text column TRY_CASTing to NULL)
    # catches both swapped and dead total columns.
    if total_scrap > 0 and total_scrap > total_units:
        warnings.append(
            "Scrap units exceed total units — the scrap and total columns are likely swapped or mismapped."
        )
    if total_units < 0 or total_scrap < 0:
        warnings.append("Negative unit totals found — check the column mapping.")

    by_reason: list[dict] = []
    if reason_groups:
        sorted_reasons = sorted(reason_groups.items(), key=lambda x: x[1], reverse=True)
        grand_scrap = sum(v for _, v in sorted_reasons) or 1.0
        cumulative = 0.0
        for reason, count in sorted_reasons:
            pct = round(count / grand_scrap * 100, 1)
            cumulative += pct
            by_reason.append({
                "reason": reason,
                "count": round(count, 2),
                "pct_of_total": pct,
                "cumulative_pct": round(min(cumulative, 100.0), 1),
            })

    by_machine = sorted(
        [
            {
                "machine": m,
                "total_units": round(g["total"], 2),
                "scrap_units": round(g["scrap"], 2),
                "scrap_rate_pct": round(g["scrap"] / g["total"] * 100, 2) if g["total"] > 0 else 0.0,
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
        "by_reason": by_reason,
        "by_machine": by_machine,
        "trend": trend,
        "warnings": warnings,
    }
