"""Alert rules: threshold checks scanned on demand against a Dataset (machine
downtime, quality, inventory, production target) or a live realtime stream
(stream_metric). Ported from the desktop app's routes/alerts.py, rewritten
against this app's Dataset/DuckDB layer (app.services.datasets) instead of
the desktop's ad-hoc CSV/parquet reader, and app.analytics.oee_calc's
numeric-coercion SQL (val_expr) instead of a Python-side _safe_float loop.

Trigger evaluations are computed live and never persisted - only the rules
themselves (app.models.alert.AlertRule) are stored.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import val_expr
from app.models.alert import AlertRule
from app.models.dataset import Dataset
from app.services import datasets as ds_service

_MAX_OFFENDERS_SHOWN = 5


# ─── Rule CRUD ──────────────────────────────────────────────────────────────

async def list_rules(db: AsyncSession, org_id: uuid.UUID) -> list[AlertRule]:
    result = await db.execute(select(AlertRule).where(AlertRule.org_id == org_id).order_by(AlertRule.created_at))
    return list(result.scalars().all())


async def create_rule(
    db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID, name: str, type: str, severity: str,
    dataset_id: uuid.UUID | None, config: dict, enabled: bool = True,
) -> AlertRule:
    rule = AlertRule(
        org_id=org_id, created_by=created_by, name=name, type=type, severity=severity,
        dataset_id=dataset_id, config=config, enabled=enabled,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def update_rule(db: AsyncSession, rule: AlertRule, patch: dict) -> AlertRule:
    for key, value in patch.items():
        setattr(rule, key, value)
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, rule: AlertRule) -> None:
    await db.delete(rule)
    await db.commit()


# ─── Trigger result ─────────────────────────────────────────────────────────

@dataclass
class AlertTrigger:
    rule_id: str
    rule_name: str
    type: str
    severity: str
    triggered: bool
    value: float
    threshold: float
    message: str
    offenders: list[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _missing_columns(dataset: Dataset, cols: dict[str, str | None]) -> str | None:
    """A soft warning (not an HTTPException) - an alert rule with a stale/unset
    column mapping should show up as a failed trigger, not 500 the scan."""
    missing = set(ds_service.missing_columns(dataset, cols.values()))
    labels = [label for label, col in cols.items() if col in missing]
    return f"Column(s) not found in dataset: {', '.join(labels)}" if labels else None


def _time_filter_sql(cfg: dict) -> str:
    time_col = (cfg.get("time_col") or "").strip()
    lookback_days = int(cfg.get("lookback_days") or 0)
    if not time_col or lookback_days <= 0:
        return ""
    return (
        f" WHERE TRY_CAST({ds_service.qi(time_col)} AS TIMESTAMP) "
        f">= (CURRENT_TIMESTAMP - INTERVAL '{lookback_days} days')"
    )


def _sum_by_group(con, rel: str, value_col: str, group_col: str, where_sql: str) -> list[tuple[str, float]]:
    gq = ds_service.qi(group_col)
    sql = f"SELECT CAST({gq} AS VARCHAR), SUM({val_expr(value_col)}) FROM {rel}{where_sql} GROUP BY 1"  # nosec B608
    return [(str(row[0]), float(row[1] or 0.0)) for row in con.execute(sql).fetchall()]


def _sum_total(con, rel: str, value_col: str, where_sql: str) -> float:
    sql = f"SELECT SUM({val_expr(value_col)}) FROM {rel}{where_sql}"  # nosec B608
    row = con.execute(sql).fetchone()
    return float(row[0] or 0.0) if row else 0.0


def _sum2_by_group(
    con, rel: str, col_a: str, col_b: str, group_col: str, where_sql: str,
) -> list[tuple[str, float, float]]:
    gq = ds_service.qi(group_col)
    sql = (
        f"SELECT CAST({gq} AS VARCHAR), SUM({val_expr(col_a)}), SUM({val_expr(col_b)}) "  # nosec B608
        f"FROM {rel}{where_sql} GROUP BY 1"
    )
    return [(str(row[0]), float(row[1] or 0.0), float(row[2] or 0.0)) for row in con.execute(sql).fetchall()]


def _sum2_total(con, rel: str, col_a: str, col_b: str, where_sql: str) -> tuple[float, float]:
    sql = f"SELECT SUM({val_expr(col_a)}), SUM({val_expr(col_b)}) FROM {rel}{where_sql}"  # nosec B608
    row = con.execute(sql).fetchone()
    return (float(row[0] or 0.0), float(row[1] or 0.0)) if row else (0.0, 0.0)


def _offenders_suffix(offenders: list[str]) -> str:
    return f" — {', '.join(offenders[:_MAX_OFFENDERS_SHOWN])}" if offenders else ""


# ─── Evaluators ─────────────────────────────────────────────────────────────

def _eval_machine_downtime(rule: AlertRule, dataset: Dataset, con, rel: str) -> AlertTrigger:
    cfg = rule.config
    duration_col = cfg.get("duration_col", "")
    machine_col = cfg.get("machine_col") or ""
    threshold = float(cfg.get("threshold", 60))

    col_warn = _missing_columns(dataset, {"duration": duration_col, "machine": machine_col})
    if col_warn:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, col_warn)

    where_sql = _time_filter_sql(cfg)
    if machine_col:
        by_machine = _sum_by_group(con, rel, duration_col, machine_col, where_sql)
        offenders = sorted(m for m, total in by_machine if total > threshold)
        value = max((total for _, total in by_machine), default=0.0)
        message = f"Max downtime {value:.1f} min (threshold: {threshold:.1f} min)"
    else:
        value = _sum_total(con, rel, duration_col, where_sql)
        offenders = [] if value <= threshold else ["All"]
        message = f"Total downtime {value:.1f} min (threshold: {threshold:.1f} min)"

    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, bool(offenders), round(value, 2), threshold,
        message + _offenders_suffix(offenders), offenders,
    )


def _eval_quality(rule: AlertRule, dataset: Dataset, con, rel: str) -> AlertTrigger:
    cfg = rule.config
    good_col = cfg.get("good_units_col", "")
    total_col = cfg.get("total_units_col", "")
    machine_col = cfg.get("machine_col") or ""
    threshold_pct = float(cfg.get("defect_threshold_pct", 5.0))

    col_warn = _missing_columns(dataset, {"good_units": good_col, "total_units": total_col, "machine": machine_col})
    if col_warn:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold_pct, col_warn)

    where_sql = _time_filter_sql(cfg)
    if machine_col:
        by_m = _sum2_by_group(con, rel, good_col, total_col, machine_col, where_sql)
        rates = {m: (1 - g / t) * 100 if t > 0 else 0.0 for m, g, t in by_m}
        offenders = sorted(m for m, rate in rates.items() if rate > threshold_pct)
        value = max(rates.values(), default=0.0)
    else:
        total_good, total_all = _sum2_total(con, rel, good_col, total_col, where_sql)
        value = (1 - total_good / total_all) * 100 if total_all > 0 else 0.0
        offenders = [] if value <= threshold_pct else ["All"]

    message = f"Defect rate {value:.1f}% (threshold: {threshold_pct:.1f}%)" + _offenders_suffix(offenders)
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, bool(offenders), round(value, 2), threshold_pct,
        message, offenders,
    )


def _eval_inventory(rule: AlertRule, dataset: Dataset, con, rel: str) -> AlertTrigger:
    cfg = rule.config
    qty_col = cfg.get("quantity_col", "")
    item_col = cfg.get("item_col") or ""
    threshold = float(cfg.get("min_threshold", 100))

    col_warn = _missing_columns(dataset, {"quantity": qty_col, "item": item_col})
    if col_warn:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, col_warn)

    where_sql = _time_filter_sql(cfg)
    if item_col:
        by_item = _sum_by_group(con, rel, qty_col, item_col, where_sql)
        offenders = sorted(i for i, qty in by_item if qty < threshold)
        value = min((qty for _, qty in by_item), default=0.0)
    else:
        value = _sum_total(con, rel, qty_col, where_sql)
        offenders = [] if value >= threshold else ["All"]

    suffix = _offenders_suffix(offenders)
    message = f"Min stock {value:.0f} units (threshold: {threshold:.0f})" + suffix
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, bool(offenders), round(value, 2), threshold,
        message, offenders,
    )


def _eval_production_target(rule: AlertRule, dataset: Dataset, con, rel: str) -> AlertTrigger:
    cfg = rule.config
    actual_col = cfg.get("actual_col", "")
    target_col = cfg.get("target_col") or ""
    target_value = cfg.get("target_value")
    group_col = cfg.get("group_col") or ""
    miss_threshold = float(cfg.get("miss_threshold_pct", 5.0))

    if not target_col and not target_value:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, miss_threshold,
            "No target configured — set a target column or flat target value.",
        )

    col_warn = _missing_columns(dataset, {"actual": actual_col, "target": target_col, "group": group_col})
    if col_warn:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, miss_threshold, col_warn)

    where_sql = _time_filter_sql(cfg)
    if group_col:
        rows = (
            _sum2_by_group(con, rel, actual_col, target_col, group_col, where_sql) if target_col
            else [(g, actual, 0.0) for g, actual in _sum_by_group(con, rel, actual_col, group_col, where_sql)]
        )
        offenders: list[str] = []
        worst = 0.0
        for g, actual, tgt in rows:
            t = tgt if target_col else float(target_value or 0)
            if t > 0:
                miss = (1 - actual / t) * 100
                worst = max(worst, miss)
                if miss > miss_threshold:
                    offenders.append(g)
        value = worst
    else:
        total_actual = _sum_total(con, rel, actual_col, where_sql)
        total_target = _sum_total(con, rel, target_col, where_sql) if target_col else float(target_value or 0)
        value = (1 - total_actual / total_target) * 100 if total_target > 0 else 0.0
        offenders = [] if value <= miss_threshold else ["All"]

    message = f"Production {value:.1f}% below target (threshold: {miss_threshold:.1f}%)" + _offenders_suffix(offenders)
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, bool(offenders), round(value, 2), miss_threshold,
        message, offenders,
    )


def eval_stream_metric(rule: AlertRule) -> AlertTrigger:
    """Targets a live app.services.realtime StreamRunner instead of a dataset -
    same threshold/offender shape as the dataset evaluators above, reading the
    live in-memory snapshot instead of querying a Dataset file."""
    from app.services.realtime import get_runner

    cfg = rule.config
    stream_id = cfg.get("stream_id", "")
    metric_name = cfg.get("metric_name", "")
    comparison = cfg.get("comparison", "gt")  # "gt" | "lt"
    threshold = float(cfg.get("threshold", 0))

    runner = get_runner(uuid.UUID(stream_id)) if stream_id else None
    if runner is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "Stream not running — nothing to evaluate.",
        )

    snap = runner.snapshot()
    metrics = snap.get("metrics", {})
    if metric_name not in metrics:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            f"Metric '{metric_name}' not available on this stream yet.",
        )

    value = float(metrics.get(metric_name) or 0.0)
    triggered = (value > threshold) if comparison == "gt" else (value < threshold)
    op_word = "above" if comparison == "gt" else "below"
    message = (
        f"{metric_name} = {value:g} ({op_word} threshold {threshold:g})" if triggered
        else f"{metric_name} = {value:g} (within threshold)"
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(value, 4), threshold,
        message, [snap.get("name", stream_id)] if triggered else [],
    )


_DATASET_EVALUATORS = {
    "machine_downtime": _eval_machine_downtime,
    "quality": _eval_quality,
    "inventory": _eval_inventory,
    "production_target": _eval_production_target,
}


def _materialize(con, dataset: Dataset) -> str:
    """Read a dataset's file into an in-memory table once per connection, so N
    rules sharing one dataset in a single scan_all call don't each re-parse
    the file from disk via their own read_csv_auto/read_parquet query."""
    rel = ds_service.dataset_relation(dataset)
    con.execute(f"CREATE TEMP TABLE ds AS SELECT * FROM {rel}")  # nosec B608 - rel is dataset_relation()'s quote-escaped relation string
    return "ds"


async def evaluate_rule(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule,
    *, dataset: Dataset | None = None, con=None, rel: str | None = None,
) -> AlertTrigger:
    if rule.type == "stream_metric":
        return eval_stream_metric(rule)
    evaluator = _DATASET_EVALUATORS.get(rule.type)
    if evaluator is None:
        raise ValueError(f"Unknown alert type: {rule.type}")
    if rule.dataset_id is None:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, "No dataset configured.")
    if dataset is None:
        dataset = await ds_service.get_dataset_or_404(db, org_id, rule.dataset_id)
    if con is not None:
        return evaluator(rule, dataset, con, rel)
    con = ds_service.open_con()
    try:
        return evaluator(rule, dataset, con, ds_service.dataset_relation(dataset))
    finally:
        con.close()


async def evaluate_rule_safe(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule,
    *, dataset: Dataset | None = None, con=None, rel: str | None = None,
) -> AlertTrigger:
    """evaluate_rule, with a bad/stale rule config (e.g. a required column left
    unset) turned into a failed AlertTrigger instead of a raised exception -
    shared by scan_all and the single-rule "Test" scan so both give the same
    graceful result instead of one of them 500ing."""
    try:
        return await evaluate_rule(db, org_id, rule, dataset=dataset, con=con, rel=rel)
    except Exception as exc:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, f"Error: {exc}")


async def scan_all(db: AsyncSession, org_id: uuid.UUID) -> list[AlertTrigger]:
    rules = [r for r in await list_rules(db, org_id) if r.enabled]

    # Group dataset-backed rules by dataset so N rules sharing one dataset
    # read its file from disk once instead of N times.
    by_dataset: dict[uuid.UUID, list[AlertRule]] = {}
    solo: list[AlertRule] = []
    for rule in rules:
        if rule.type != "stream_metric" and rule.dataset_id is not None:
            by_dataset.setdefault(rule.dataset_id, []).append(rule)
        else:
            solo.append(rule)

    results = [await evaluate_rule_safe(db, org_id, rule) for rule in solo]

    for dataset_id, dataset_rules in by_dataset.items():
        try:
            dataset = await ds_service.get_dataset_or_404(db, org_id, dataset_id)
            con = ds_service.open_con()
            try:
                rel = _materialize(con, dataset)
                for rule in dataset_rules:
                    results.append(await evaluate_rule_safe(db, org_id, rule, dataset=dataset, con=con, rel=rel))
            finally:
                con.close()
        except Exception as exc:
            for rule in dataset_rules:
                results.append(AlertTrigger(
                    str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, f"Error: {exc}",
                ))
    return results


async def stream_alerts(db: AsyncSession, org_id: uuid.UUID, stream_id: uuid.UUID) -> list[AlertTrigger]:
    """Enabled stream_metric rules targeting this stream, evaluated live - used
    by realtime.py's SSE feed to attach alarm state to each tick snapshot."""
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["stream_id"].as_string() == str(stream_id),
        )
    )
    return [eval_stream_metric(r) for r in result.scalars().all()]


async def stream_alerts_bulk(
    db: AsyncSession, org_id: uuid.UUID, stream_ids: list[uuid.UUID],
) -> dict[str, list[AlertTrigger]]:
    """stream_alerts for N streams in one query - used by realtime.py's
    list_streams so listing streams isn't a query per stream."""
    if not stream_ids:
        return {}
    sids = [str(s) for s in stream_ids]
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["stream_id"].as_string().in_(sids),
        )
    )
    by_stream: dict[str, list[AlertTrigger]] = {}
    for rule in result.scalars().all():
        by_stream.setdefault(rule.config.get("stream_id"), []).append(eval_stream_metric(rule))
    return by_stream
