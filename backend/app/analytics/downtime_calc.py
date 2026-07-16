# app/analytics/downtime_calc.py
#
# Downtime aggregation engine — ported near-verbatim from the desktop app
# (evowerk-console backend/routes/downtime.py). Pure DuckDB-SQL builders +
# bounded aggregations; the service layer supplies the relation + connection.

from __future__ import annotations

from app.analytics.oee_calc import inv_expr, val_expr


def dt_overall(con, rel: str, dcol: str) -> dict:
    # nosec B608 - dcol is validate_columns-checked by the route before reaching
    # here; dur/inv are qi()-quoted via val_expr/inv_expr, not raw dcol.
    # val_expr floors physically-impossible negative durations to 0 (a negative
    # duration can't be a real incident) and inv_expr counts them as data issues,
    # same convention as oee_calc's OEE columns.
    dur, inv = val_expr(dcol), inv_expr(dcol)
    sql = (
        f"SELECT COUNT(*) FILTER (WHERE {dur} > 0), COALESCE(SUM({dur}) FILTER (WHERE {dur} > 0), 0.0), "  # nosec B608
        f"SUM({inv}), SUM(CASE WHEN {inv} > 0 THEN 1 ELSE 0 END) FROM {rel}"
    )
    r = con.execute(sql).fetchone() or (0, 0.0, 0, 0)
    return {
        "incidents": int(r[0] or 0), "minutes_raw": float(r[1] or 0.0),
        "invalid_cells": int(r[2] or 0), "rows_with_issues": int(r[3] or 0),
    }


def dt_grouped(con, rel: str, dcol: str, key_expr: str) -> list:
    """Group incidents (duration > 0) and invalid cells by key_expr; NULL keys
    are skipped. A row joins a group if it's a real incident OR a data-quality
    issue (negative/unparseable duration) — otherwise a machine/category whose
    duration data is entirely garbled would vanish instead of surfacing its
    invalid_cells count (same reasoning as oee_grouped's per-group bad count)."""
    dur, inv = val_expr(dcol), inv_expr(dcol)
    # nosec B608 - dur/inv are qi()-quoted via val_expr/inv_expr; key_expr is built
    # by the caller from qi()-quoted, validate_columns-checked columns.
    sql = (
        f"SELECT {key_expr} AS k, COUNT(*) FILTER (WHERE {dur} > 0), "  # nosec B608
        f"COALESCE(SUM({dur}) FILTER (WHERE {dur} > 0), 0.0), SUM({inv}) "
        f"FROM {rel} WHERE ({dur} > 0 OR {inv} > 0) AND ({key_expr}) IS NOT NULL GROUP BY 1"
    )
    out = []
    for row in con.execute(sql).fetchall():
        out.append((str(row[0]), {
            "incidents": int(row[1] or 0),
            "minutes_raw": float(row[2] or 0.0),
            "invalid_cells": int(row[3] or 0),
        }))
    return out
