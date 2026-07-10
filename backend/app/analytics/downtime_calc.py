# app/analytics/downtime_calc.py
#
# Downtime aggregation engine — ported near-verbatim from the desktop app
# (evowerk-console backend/routes/downtime.py). Pure DuckDB-SQL builders +
# bounded aggregations; the service layer supplies the relation + connection.

from __future__ import annotations

from app.analytics.oee_calc import num_invalid, num_val


def dt_overall(con, rel: str, dcol: str) -> dict:
    # nosec B608 - dcol is validate_columns-checked by the route before reaching
    # here; dur/inv are qi()-quoted via num_val/num_invalid, not raw dcol.
    dur, inv = num_val(dcol), num_invalid(dcol)
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
    """Group incidents (duration > 0) by key_expr; NULL keys are skipped."""
    dur = num_val(dcol)
    # nosec B608 - dur is qi()-quoted via num_val; key_expr is built by the
    # caller from qi()-quoted, validate_columns-checked columns.
    sql = (
        f"SELECT {key_expr} AS k, COUNT(*), COALESCE(SUM({dur}), 0.0) "  # nosec B608
        f"FROM {rel} WHERE {dur} > 0 AND ({key_expr}) IS NOT NULL GROUP BY 1"
    )
    out = []
    for row in con.execute(sql).fetchall():
        out.append((str(row[0]), {"incidents": int(row[1] or 0), "minutes_raw": float(row[2] or 0.0)}))
    return out
