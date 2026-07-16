"""KPI compute/compare/evaluate — ported from the desktop app's
kpis_engine.py. The desktop version executed KPI SQL itself (raw sqlite temp
tables or a DB client per dataset source); here every KPI is a plain SQL
aggregate over a Dataset, so it reuses app.services.sql_query.run_sql, which
already does the single-tenant safety work (single-SELECT validation,
materialization, timeout, row cap) that the desktop engine had to hand-roll.

Values are cached forever until `force=True` (same semantics as the desktop
engine) — this is a snapshot history, not a TTL cache: recomputation is
always explicit.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, stdev
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.kpi import Kpi, KpiValue
from app.services import datasets as ds_service
from app.services.sql_query import run_sql

# A baseline window is a SAMPLE of readings used to estimate the underlying
# process's mean/variance, not the full population — sample std (N-1) is the
# unbiased estimator here, not population std. Below this many points, both
# the mean and std are themselves too unstable to trust a z-score against.
_MIN_BASELINE_N = 5


@dataclass
class KpiComputeItem:
    kpi_id: uuid.UUID
    value: float | None
    meta: dict
    computed_at: datetime
    cache_hit: bool
    error: str | None = None
    eval: dict | None = None
    breakdown: list[tuple[str, float]] | None = None


@dataclass
class KpiCompareItem:
    kpi_id: uuid.UUID
    a_value: float | None
    b_value: float | None
    delta: float | None
    pct_delta: float | None
    a_error: str | None
    b_error: str | None
    a_eval: dict | None = None
    b_eval: dict | None = None


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _replace_table_placeholder(sql: str) -> str:
    if "{table}" not in sql:
        raise ValueError("KPI sql must include a '{table}' placeholder")
    return sql.replace("{table}", "dataset")


_BREAKDOWN_AGG_SQL = {"avg": "AVG", "sum": "SUM"}


def _breakdown_sql(group_col: str, agg: str, value_col: str | None) -> str:
    # nosec B608 - group_col/value_col reach here only after the caller
    # (_compute_breakdown) has run them through ds_service.validate_columns,
    # the same allow-list every other structured SQL builder in this codebase
    # uses before qi()-quoting; there is no unvalidated identifier here.
    group_ref = ds_service.qi(group_col)
    if agg == "count":
        return f"SELECT {group_ref}, COUNT(*) FROM {{table}} GROUP BY {group_ref} ORDER BY 2 DESC"  # nosec B608
    value_ref = ds_service.qi(value_col)
    return (
        f"SELECT {group_ref}, {_BREAKDOWN_AGG_SQL[agg]}(TRY_CAST({value_ref} AS DOUBLE)) "  # nosec B608
        f"FROM {{table}} GROUP BY {group_ref} ORDER BY 2 DESC"
    )


async def _compute_breakdown(dataset: Dataset, kpi: Kpi) -> list[tuple[str, float]] | None:
    """Runs alongside the main KPI value when a breakdown is configured -
    per-machine averages or a breach-cause Pareto, depending on what column
    the author picked. Fails soft: a bad breakdown query degrades to no
    breakdown rather than failing the whole KPI compute."""
    if not kpi.breakdown_group_col:
        return None
    agg = kpi.breakdown_agg or "count"
    if agg not in ("count", "avg", "sum"):
        return None
    try:
        ds_service.validate_columns(
            dataset,
            [kpi.breakdown_group_col, kpi.breakdown_value_col] if kpi.breakdown_value_col else [kpi.breakdown_group_col],
        )
        sql = _replace_table_placeholder(
            _breakdown_sql(kpi.breakdown_group_col, agg, kpi.breakdown_value_col)
        )
        result = await run_sql(dataset, sql, row_limit=10)
    except (HTTPException, ValueError):
        return None
    out: list[tuple[str, float]] = []
    for row in result["rows"]:
        vals = list(row.values())
        if len(vals) < 2:
            continue
        v = _safe_float(vals[1])
        if v is not None:
            out.append((str(vals[0]), v))
    return out or None


def _delta(prev: float | None, curr: float | None) -> tuple[float | None, float | None]:
    if prev is None or curr is None:
        return None, None
    d = curr - prev
    pct = d / prev if prev != 0 else None
    return d, pct


def _trend(values: list[float], eps_pct: float) -> str:
    if len(values) < 2:
        return "unknown"
    first, last = values[0], values[-1]
    if first == 0:
        return "unknown"
    change = (last - first) / abs(first)
    if abs(change) <= eps_pct:
        return "flat"
    return "up" if change > 0 else "down"


def _baseline_stats(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < _MIN_BASELINE_N:
        return None, None
    try:
        return mean(values), stdev(values)
    except Exception:
        return None, None


_RANK = {"unknown": 0, "ok": 1, "warn": 2, "breach": 3}


def _evaluate_status(
    policy: dict | None,
    value: float | None,
    prev: float | None,
    history: list[float],
) -> dict:
    """Status must only ever ESCALATE across rule types: a genuine breach
    from one rule must never be downgraded to warn by a milder signal from
    another. `_bump` records every fired reason but keeps the highest
    severity reached."""
    ev: dict[str, Any] = {
        "value": value,
        "prev_value": prev,
        "status": "unknown",
        "reasons": [],
        "delta_abs": None,
        "delta_pct": None,
        "baseline_window": None,
        "baseline_mean": None,
        "baseline_std": None,
        "zscore": None,
        "trend_window": None,
        "trend": "unknown",
    }

    if not policy or value is None:
        return ev

    d_abs, d_pct = _delta(prev, value)
    ev["delta_abs"] = d_abs
    ev["delta_pct"] = d_pct

    def _bump(new_status: str, reason: str) -> None:
        ev["reasons"].append(reason)
        if _RANK[new_status] > _RANK[ev["status"]]:
            ev["status"] = new_status

    delta_p = policy.get("delta") or {}
    if delta_p and d_abs is not None:
        if delta_p.get("breach_abs") is not None and abs(d_abs) >= delta_p["breach_abs"]:
            _bump("breach", "delta_abs_breach")
        elif delta_p.get("warn_abs") is not None and abs(d_abs) >= delta_p["warn_abs"]:
            _bump("warn", "delta_abs_warn")

        if d_pct is not None:
            if delta_p.get("breach_pct") is not None and abs(d_pct) >= delta_p["breach_pct"]:
                _bump("breach", "delta_pct_breach")
            elif delta_p.get("warn_pct") is not None and abs(d_pct) >= delta_p["warn_pct"]:
                _bump("warn", "delta_pct_warn")

    static_p = policy.get("static") or {}
    if static_p:
        if static_p.get("breach_low") is not None and value <= static_p["breach_low"]:
            _bump("breach", "static_breach_low")
        if static_p.get("breach_high") is not None and value >= static_p["breach_high"]:
            _bump("breach", "static_breach_high")
        if static_p.get("warn_low") is not None and value <= static_p["warn_low"]:
            _bump("warn", "static_warn_low")
        if static_p.get("warn_high") is not None and value >= static_p["warn_high"]:
            _bump("warn", "static_warn_high")

    baseline_p = policy.get("baseline") or {}
    if baseline_p and history:
        win = baseline_p.get("window", 14)
        # Exclude the current observation (most recent point in history) from
        # its own baseline — otherwise the value is folded into the mean/std
        # it's compared against, shrinking its z-score and masking anomalies.
        prior = history[:-1] if history else []
        vals = prior[-win:]
        mu, sd = _baseline_stats(vals)
        ev["baseline_window"] = win
        ev["baseline_mean"] = mu
        ev["baseline_std"] = sd
        if mu is not None and sd and sd > 0:
            z = (value - mu) / sd
            ev["zscore"] = z
            if baseline_p.get("breach_z") is not None and abs(z) >= baseline_p["breach_z"]:
                _bump("breach", "baseline_breach_z")
            elif baseline_p.get("warn_z") is not None and abs(z) >= baseline_p["warn_z"]:
                _bump("warn", "baseline_warn_z")

    trend_p = policy.get("trend") or {}
    if trend_p and history:
        win = trend_p.get("window", 14)
        vals = history[-win:]
        ev["trend_window"] = win
        ev["trend"] = _trend(vals, trend_p.get("flat_epsilon_pct", 0.01))

    if ev["status"] == "unknown":
        ev["status"] = "ok"

    return ev


async def get_history_values(
    db: AsyncSession, org_id: uuid.UUID, kpi_id: uuid.UUID, dataset_id: uuid.UUID, limit: int
) -> list[KpiValue]:
    """Newest-first."""
    rows = (
        await db.execute(
            select(KpiValue)
            .where(KpiValue.org_id == org_id, KpiValue.kpi_id == kpi_id, KpiValue.dataset_id == dataset_id)
            .order_by(KpiValue.computed_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def _get_latest_value(
    db: AsyncSession, org_id: uuid.UUID, kpi_id: uuid.UUID, dataset_id: uuid.UUID
) -> KpiValue | None:
    return (
        await db.execute(
            select(KpiValue)
            .where(KpiValue.org_id == org_id, KpiValue.kpi_id == kpi_id, KpiValue.dataset_id == dataset_id)
            .order_by(KpiValue.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _compute_one(
    db: AsyncSession,
    org_id: uuid.UUID,
    kpi: Kpi,
    dataset: Dataset,
    *,
    force: bool,
    include_eval: bool,
    lookback: int,
) -> KpiComputeItem:
    cached = None if force else await _get_latest_value(db, org_id, kpi.id, dataset.id)

    if cached is not None and cached.error is None:
        history = await get_history_values(db, org_id, kpi.id, dataset.id, lookback + 1)
        prev = history[1].value if len(history) > 1 else None
        hist_vals = [h.value for h in reversed(history) if h.value is not None]
        eval_res = (
            _evaluate_status(kpi.eval_policy, cached.value, prev, hist_vals) if include_eval else None
        )
        # "breakdown" is only present in meta for values computed after this
        # caching was added — older rows fall back to a live recompute so
        # they don't break, but every value computed from here on caches it.
        if "breakdown" in cached.meta:
            raw = cached.meta.get("breakdown")
            breakdown = [(b["label"], b["value"]) for b in raw] if raw else None
        else:
            breakdown = await _compute_breakdown(dataset, kpi)
        return KpiComputeItem(
            kpi_id=kpi.id,
            value=cached.value,
            meta=cached.meta,
            computed_at=cached.computed_at,
            cache_hit=True,
            error=None,
            eval=eval_res,
            breakdown=breakdown,
        )

    if not kpi.enabled:
        kv = KpiValue(org_id=org_id, kpi_id=kpi.id, dataset_id=dataset.id, value=None, error="KPI is disabled")
        db.add(kv)
        await db.flush()
        return KpiComputeItem(kpi_id=kpi.id, value=None, meta={}, computed_at=kv.computed_at, cache_hit=False, error=kv.error)

    value: float | None = None
    meta: dict = {}
    error: str | None = None
    try:
        q = _replace_table_placeholder(kpi.sql)
        result = await run_sql(dataset, q, row_limit=1)
        rows = result["rows"]
        if rows:
            value = _safe_float(next(iter(rows[0].values()), None))
        meta = {"columns": result["columns"]}
    except HTTPException as exc:
        error = str(exc.detail)
    except ValueError as exc:
        error = str(exc)

    breakdown: list[tuple[str, float]] | None = None
    if error is None:
        breakdown = await _compute_breakdown(dataset, kpi)
        # Cache the breakdown alongside the value so cache-hit polls (as
        # frequent as every 15s for operator dashboards) don't re-open a
        # DuckDB connection and re-materialize the dataset just for this.
        meta["breakdown"] = [{"label": label, "value": v} for label, v in breakdown] if breakdown else None

    kv = KpiValue(org_id=org_id, kpi_id=kpi.id, dataset_id=dataset.id, value=value, error=error, meta=meta)
    db.add(kv)
    await db.flush()

    if error is not None:
        return KpiComputeItem(kpi_id=kpi.id, value=None, meta={}, computed_at=kv.computed_at, cache_hit=False, error=error)

    history = await get_history_values(db, org_id, kpi.id, dataset.id, lookback + 1)
    prev = history[1].value if len(history) > 1 else None
    hist_vals = [h.value for h in reversed(history) if h.value is not None]
    eval_res = _evaluate_status(kpi.eval_policy, value, prev, hist_vals) if include_eval else None

    return KpiComputeItem(
        kpi_id=kpi.id, value=value, meta=meta, computed_at=kv.computed_at, cache_hit=False, error=None,
        eval=eval_res, breakdown=breakdown,
    )


async def compute_kpis(
    db: AsyncSession,
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    kpi_ids: list[uuid.UUID],
    *,
    force: bool = False,
    include_eval: bool = False,
    lookback: int = 14,
) -> list[KpiComputeItem]:
    dataset = await ds_service.get_dataset_or_404(db, org_id, dataset_id)

    kpis_by_id: dict[uuid.UUID, Kpi] = {}
    if kpi_ids:
        rows = (
            await db.execute(select(Kpi).where(Kpi.org_id == org_id, Kpi.id.in_(kpi_ids)))
        ).scalars().all()
        kpis_by_id = {k.id: k for k in rows}

    items: list[KpiComputeItem] = []
    for kpi_id in kpi_ids:
        kpi = kpis_by_id.get(kpi_id)
        if kpi is None:
            items.append(
                KpiComputeItem(kpi_id=kpi_id, value=None, meta={}, computed_at=datetime.now(timezone.utc), cache_hit=False, error="KPI not found")
            )
            continue
        items.append(
            await _compute_one(db, org_id, kpi, dataset, force=force, include_eval=include_eval, lookback=lookback)
        )

    await db.commit()
    return items


async def compare_kpis(
    db: AsyncSession,
    org_id: uuid.UUID,
    dataset_id_a: uuid.UUID,
    dataset_id_b: uuid.UUID,
    kpi_ids: list[uuid.UUID],
    *,
    force: bool = False,
    include_eval: bool = False,
    lookback: int = 14,
) -> list[KpiCompareItem]:
    a_items = await compute_kpis(db, org_id, dataset_id_a, kpi_ids, force=force, include_eval=include_eval, lookback=lookback)
    b_items = await compute_kpis(db, org_id, dataset_id_b, kpi_ids, force=force, include_eval=include_eval, lookback=lookback)

    a_map = {i.kpi_id: i for i in a_items}
    b_map = {i.kpi_id: i for i in b_items}

    out: list[KpiCompareItem] = []
    for kpi_id in kpi_ids:
        a = a_map.get(kpi_id)
        b = b_map.get(kpi_id)

        delta = None
        pct = None
        if a and b and a.value is not None and b.value is not None:
            delta = b.value - a.value
            if a.value != 0:
                pct = delta / a.value * 100.0

        out.append(
            KpiCompareItem(
                kpi_id=kpi_id,
                a_value=a.value if a else None,
                b_value=b.value if b else None,
                delta=delta,
                pct_delta=pct,
                a_error=a.error if a else "missing",
                b_error=b.error if b else "missing",
                a_eval=a.eval if a else None,
                b_eval=b.eval if b else None,
            )
        )
    return out


async def compute_kpis_multi_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    kpi_ids: list[uuid.UUID],
    *,
    force: bool = False,
    include_eval: bool = False,
    lookback: int = 14,
) -> list[KpiComputeItem]:
    """Like compute_kpis, but each KPI runs against its OWN dataset_id rather
    than one shared dataset_id - needed for a dashboard whose KPIs span
    multiple datasets. Groups by dataset_id and calls compute_kpis once per
    group, so the single-dataset code path is unchanged."""
    rows = (
        await db.execute(select(Kpi.id, Kpi.dataset_id).where(Kpi.org_id == org_id, Kpi.id.in_(kpi_ids)))
    ).all()
    dataset_by_kpi = {r.id: r.dataset_id for r in rows}

    by_dataset: dict[uuid.UUID, list[uuid.UUID]] = {}
    for kpi_id in kpi_ids:
        dataset_id = dataset_by_kpi.get(kpi_id)
        if dataset_id is None:
            continue
        by_dataset.setdefault(dataset_id, []).append(kpi_id)

    items_by_id: dict[uuid.UUID, KpiComputeItem] = {}
    for dataset_id, ids in by_dataset.items():
        for item in await compute_kpis(db, org_id, dataset_id, ids, force=force, include_eval=include_eval, lookback=lookback):
            items_by_id[item.kpi_id] = item

    out: list[KpiComputeItem] = []
    for kpi_id in kpi_ids:
        if kpi_id in items_by_id:
            out.append(items_by_id[kpi_id])
        else:
            out.append(
                KpiComputeItem(
                    kpi_id=kpi_id, value=None, meta={}, computed_at=datetime.now(timezone.utc),
                    cache_hit=False, error="KPI not found",
                )
            )
    return out
