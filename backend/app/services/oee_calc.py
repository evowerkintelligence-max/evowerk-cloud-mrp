"""OEE (Overall Equipment Effectiveness) math engine — the single source of
truth for Availability / Performance / Quality / OEE% computation, and the
DuckDB numeric-coercion rules behind it.

OEE = Availability × Performance × Quality

Ported from the desktop app's backend/oee_calc.py; relation-building and SQL
quoting are reused from app.services.datasets instead of duplicated here.
"""
from __future__ import annotations

from typing import Optional

from app.services.datasets import qi, qs


def val_expr(col: str) -> str:
    # blank -> 0 (valid); strip thousands commas; unparseable -> 0; negative -> 0.
    # Time/unit columns (planned/operating/total/good) can never be physically
    # negative — without this floor, a stray negative cell (a sign error from an
    # upstream export, a correction row) drags Availability/Performance/Quality
    # negative, and two negative factors can multiply back into a deceptively
    # "plausible" positive OEE%. inv_expr counts these same cells as issues.
    c = qi(col)
    return (
        f"GREATEST(0.0, COALESCE(TRY_CAST(NULLIF(REPLACE(TRIM(CAST({c} AS VARCHAR)), ',', ''), '') "
        f"AS DOUBLE), 0.0))"
    )


def inv_expr(col: str) -> str:
    # 1 when the cell is a data-quality issue: non-blank but unparseable, OR a
    # parseable but NEGATIVE number (physically invalid for time/unit columns —
    # val_expr zeroes these same cells out of the sums).
    c = qi(col)
    txt = f"NULLIF(TRIM(CAST({c} AS VARCHAR)), '')"
    parsed = f"TRY_CAST(REPLACE(TRIM(CAST({c} AS VARCHAR)), ',', '') AS DOUBLE)"
    return f"CASE WHEN {txt} IS NOT NULL AND ({parsed} IS NULL OR {parsed} < 0) THEN 1 ELSE 0 END"


def blank_expr(col: str) -> str:
    # 1 when the cell is blank — distinct from inv_expr's "garbled or negative"
    # issue. A blank legitimately means "no data recorded"; a garbled/negative
    # cell means "data was recorded but is wrong". Both are zeroed by val_expr,
    # but conflating their counts would hide a mostly-missing dataset behind a
    # reassuring "0 invalid cells".
    c = qi(col)
    return f"CASE WHEN NULLIF(TRIM(CAST({c} AS VARCHAR)), '') IS NULL THEN 1 ELSE 0 END"


def rate_sql_expr(
    machine_col: Optional[str],
    machine_rates: Optional[dict],
    target_rate: Optional[float],
) -> Optional[str]:
    """SQL expression yielding a row's ideal rate (units/min), or None when no
    rate information exists at all (Performance is then fully estimated).

    Per-machine rates map a machine label → its own ideal rate; rows whose
    machine has no entry fall back to the global target_rate (NULL if absent —
    unrated_expr counts those rows so the caller can flag partial estimation).
    """
    rates = {
        str(k): float(v)
        for k, v in (machine_rates or {}).items()
        if v is not None and float(v) > 0
    }
    fallback = str(float(target_rate)) if (target_rate and target_rate > 0) else "NULL"
    if machine_col and rates:
        m = qi(machine_col)
        whens = " ".join(
            f"WHEN CAST({m} AS VARCHAR) = {qs(label)} THEN {rate}"
            for label, rate in rates.items()
        )
        return f"(CASE {whens} ELSE {fallback} END)"
    if target_rate and target_rate > 0:
        return f"({float(target_rate)})"
    return None


def ideal_units_expr(o: str, t: str, rate_sql: Optional[str]) -> str:
    """Per-row ideal units = operating minutes × that row's ideal rate.

    Rows with no applicable rate contribute their ACTUAL units instead
    (performance = 100% for them) — so summing this per group degrades
    gracefully from fully-rated through partially-rated to fully-estimated.
    """
    if rate_sql is None:
        return val_expr(t)
    return f"COALESCE({val_expr(o)} * {rate_sql}, {val_expr(t)})"


def unrated_expr(rate_sql: Optional[str]) -> str:
    """1 when the row has no applicable ideal rate (its Performance is assumed 100%)."""
    if rate_sql is None:
        return "1"
    return f"(CASE WHEN {rate_sql} IS NULL THEN 1 ELSE 0 END)"


def oee_overall(
    con, rel: str, p: str, o: str, t: str, g: str, where_sql: str = "",
    ideal_sql: Optional[str] = None,
    extra_sum_cols: Optional[dict] = None,
) -> dict:
    bad = f"({inv_expr(p)}+{inv_expr(o)}+{inv_expr(t)}+{inv_expr(g)})"
    blank = f"({blank_expr(p)}+{blank_expr(o)}+{blank_expr(t)}+{blank_expr(g)})"
    clause = f" WHERE {where_sql}" if where_sql else ""
    extras = list((extra_sum_cols or {}).items())
    tail = f", SUM({ideal_units_expr(o, t, ideal_sql)}), SUM({unrated_expr(ideal_sql)})"
    tail += "".join(f", SUM({val_expr(col)})" for _, col in extras)
    # nosec B608 - p/o/t/g/rel are qi()-quoted, validate_columns-checked column
    # refs (see val_expr/inv_expr/blank_expr and the caller's validate_columns call).
    sql = (
        f"SELECT SUM({val_expr(p)}), SUM({val_expr(o)}), SUM({val_expr(t)}), SUM({val_expr(g)}), "  # nosec B608
        f"SUM({bad}), SUM(CASE WHEN {bad} > 0 THEN 1 ELSE 0 END), COUNT(*), SUM({blank})"
        f"{tail} FROM {rel}{clause}"
    )
    r = con.execute(sql).fetchone() or tuple([0] * (10 + len(extras)))
    d = {
        "planned": float(r[0] or 0.0), "operating": float(r[1] or 0.0),
        "total": float(r[2] or 0.0), "good": float(r[3] or 0.0),
        "invalid_cells": int(r[4] or 0), "rows_with_issues": int(r[5] or 0),
        "row_count": int(r[6] or 0), "blank_cells": int(r[7] or 0),
        "ideal": float(r[8] or 0.0), "unrated_rows": int(r[9] or 0),
    }
    for i, (key, _) in enumerate(extras):
        d[key] = float(r[10 + i] or 0.0)
    return d


def oee_grouped(
    con, rel: str, p: str, o: str, t: str, g: str, gcol: str,
    with_count: bool = False,
    ideal_sql: Optional[str] = None,
) -> list:
    gq = qi(gcol)
    bad = f"({inv_expr(p)}+{inv_expr(o)}+{inv_expr(t)}+{inv_expr(g)})"
    blank = f"({blank_expr(p)}+{blank_expr(o)}+{blank_expr(t)}+{blank_expr(g)})"
    extra = ", COUNT(*)" if with_count else ""
    sql = (
        f"SELECT CAST({gq} AS VARCHAR), SUM({val_expr(p)}), SUM({val_expr(o)}), "  # nosec B608
        f"SUM({val_expr(t)}), SUM({val_expr(g)}), SUM({bad}), SUM({blank}), "
        f"SUM({ideal_units_expr(o, t, ideal_sql)}), SUM({unrated_expr(ideal_sql)}){extra} "
        f"FROM {rel} WHERE NULLIF(TRIM(CAST({gq} AS VARCHAR)), '') IS NOT NULL GROUP BY 1"
    )
    out = []
    for row in con.execute(sql).fetchall():
        d = {"planned": float(row[1] or 0.0), "operating": float(row[2] or 0.0),
             "total": float(row[3] or 0.0), "good": float(row[4] or 0.0),
             "invalid_cells": int(row[5] or 0), "blank_cells": int(row[6] or 0),
             "ideal": float(row[7] or 0.0), "unrated_rows": int(row[8] or 0)}
        if with_count:
            d["count"] = float(row[9] or 0.0)
        out.append((str(row[0]), d))
    return out


def compute_components_ideal(
    planned: float,
    operating: float,
    total: float,
    good: float,
    ideal: float,
    fully_estimated: bool,
) -> tuple[float, float, float, float, bool]:
    """Availability/Performance/Quality/OEE% from pre-aggregated sums.

    Performance comes from a pre-aggregated ideal-units sum (see
    ideal_units_expr) instead of a single global target rate — this is what
    makes per-machine rates work for ANY grouping (overall, per-day trend,
    per-shift RCA dimension): each row already contributed operating × its own
    machine's rate to `ideal`, so total/ideal is the correctly blended
    Performance.

    fully_estimated means no rate info existed anywhere: Performance is
    pinned to 100%.
    """
    availability = max(0.0, min(1.0, operating / planned)) if planned > 0 else 0.0
    quality = max(0.0, min(1.0, good / total)) if total > 0 else 0.0

    if fully_estimated:
        performance = 1.0
    else:
        performance = max(0.0, min(1.0, total / ideal)) if ideal > 0 else 0.0

    oee = availability * performance * quality
    return (
        round(availability * 100, 2),
        round(performance * 100, 2),
        round(quality * 100, 2),
        round(oee * 100, 2),
        fully_estimated,
    )


def revenue_loss_ideal(
    planned: float,
    operating: float,
    total: float,
    good: float,
    ideal: float,
    unit_price: float,
) -> tuple[float, float, float]:
    """(availability_lost, performance_lost, quality_lost) in dollars, driven
    by the pre-aggregated ideal-units sum. The effective rate ideal/operating
    is the operating-time-weighted blend of per-machine rates; when rates
    were fully estimated, ideal == total so performance loss collapses to $0."""
    rate = (ideal / operating) if operating > 0 else 0.0
    avail_lost = max(0.0, (planned - operating) * rate * unit_price)
    perf_lost = max(0.0, (ideal - total) * unit_price)
    qual_lost = max(0.0, (total - good) * unit_price)
    return avail_lost, perf_lost, qual_lost
