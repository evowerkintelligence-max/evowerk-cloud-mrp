"""SPC (Statistical Process Control) — ported from the desktop app's routes/spc.py.

Individuals/Moving-Range or Xbar-R control charts + process capability
(Cp/Cpk/Pp/Ppk). Values are fetched ordered via DuckDB; the chart math is pure
(app/analytics/spc_calc.py).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.analytics import spc_calc
from app.analytics.oee_calc import qi, val_expr
from app.models.dataset import Dataset
from app.services import datasets as ds_service


def analyze_spc(dataset: Dataset, req: Any) -> dict:
    rel = ds_service.dataset_relation(dataset)
    vexpr = val_expr(req.value_col)
    sub_expr = qi(req.subgroup_col) if req.subgroup_col else None

    # ROW_NUMBER gives a reliable original-order fallback ("rowid" isn't available
    # on table functions). CSV is ALL_VARCHAR, so sort numerically when the order
    # column parses as a number, else lexically (correct for ISO date/timestamps).
    base = f"(SELECT *, ROW_NUMBER() OVER () AS __rn FROM {rel})"  # nosec B608 - rel is the quote-escaped relation string, no identifiers
    if req.order_col:
        label_expr = f"CAST({qi(req.order_col)} AS VARCHAR)"
        numeric_key = f"TRY_CAST(NULLIF(TRIM({label_expr}), '') AS DOUBLE)"
        sort_key = f"{numeric_key}, {label_expr}"
    else:
        label_expr = "CAST(__rn AS VARCHAR)"
        sort_key = "__rn"

    # nosec B608 - value_col/subgroup_col/order_col are validate_columns-checked
    # by the route before reaching here, and qi()-quoted (sub_expr/label_expr/
    # vexpr) or fixed literals (base/rel/__rn) below; no free-text SQL entry.
    con = ds_service.open_con()
    try:
        total = int((con.execute(f"SELECT COUNT(*) FROM {base}").fetchone() or [0])[0])  # nosec B608
        truncated = total > req.max_points

        if sub_expr:
            sql = (
                f"SELECT CAST({sub_expr} AS VARCHAR), {vexpr} FROM {base} "  # nosec B608
                f"WHERE NULLIF(TRIM(CAST({sub_expr} AS VARCHAR)), '') IS NOT NULL "
                f"ORDER BY {sort_key} LIMIT {req.max_points * 20}"
            )
            rows = con.execute(sql).fetchall()
            groups: dict[str, list[float]] = {}
            order: list[str] = []
            for k, v in rows:
                k = str(k)
                if k not in groups:
                    groups[k] = []
                    order.append(k)
                groups[k].append(float(v))
            order = order[: req.max_points]
            subgroups = [groups[k] for k in order if len(groups[k]) >= 2]
            labels = [k for k in order if len(groups[k]) >= 2]
            if not subgroups:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "No subgroup has 2+ members — need repeated measurements per subgroup for an Xbar-R chart.",
                )
            resp = spc_calc.xbar_r_chart(subgroups, labels)
            all_values = [v for g in subgroups for v in g]
        else:
            sql = f"SELECT {label_expr}, {vexpr} FROM {base} ORDER BY {sort_key} LIMIT {req.max_points}"  # nosec B608
            rows = con.execute(sql).fetchall()
            if len(rows) < 2:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Need at least 2 data points for a control chart.",
                )
            labels = [str(r[0]) for r in rows]
            all_values = [float(r[1]) for r in rows]
            resp = spc_calc.individuals_chart(all_values, labels)
    finally:
        con.close()

    resp["truncated"] = truncated
    if req.usl is not None or req.lsl is not None:
        mean = sum(all_values) / len(all_values)
        sigma_overall = spc_calc.std(all_values)
        d2 = spc_calc.control_constants(resp["subgroup_size"] if resp["subgroup_size"] >= 2 else 2)[3]
        sigma_within = (resp["range_centerline"] / d2) if d2 else 0.0
        resp["capability"] = spc_calc.capability(mean, sigma_within, sigma_overall, req.usl, req.lsl)

    return resp
