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

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import val_expr
from app.models.alert import AlertRule
from app.models.dataset import Dataset
from app.models.device_token import DeviceToken
from app.services import datasets as ds_service

_MAX_OFFENDERS_SHOWN = 5
_WEBHOOK_TIMEOUT = 5.0
_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_PUSH_TIMEOUT = 5.0

logger = logging.getLogger(__name__)


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


def _eval_scrap(rule: AlertRule, dataset: Dataset, con, rel: str) -> AlertTrigger:
    cfg = rule.config
    scrap_col = cfg.get("scrap_col", "")
    total_col = cfg.get("total_units_col", "")
    group_col = cfg.get("group_col") or ""
    threshold_pct = float(cfg.get("scrap_threshold_pct", 5.0))

    col_warn = _missing_columns(dataset, {"scrap_units": scrap_col, "total_units": total_col, "group": group_col})
    if col_warn:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold_pct, col_warn)

    where_sql = _time_filter_sql(cfg)
    if group_col:
        by_g = _sum2_by_group(con, rel, scrap_col, total_col, group_col, where_sql)
        rates = {g: (s / t) * 100 if t > 0 else 0.0 for g, s, t in by_g}
        offenders = sorted(g for g, rate in rates.items() if rate > threshold_pct)
        value = max(rates.values(), default=0.0)
    else:
        total_scrap, total_all = _sum2_total(con, rel, scrap_col, total_col, where_sql)
        value = (total_scrap / total_all) * 100 if total_all > 0 else 0.0
        offenders = [] if value <= threshold_pct else ["All"]

    message = f"Scrap rate {value:.1f}% (threshold: {threshold_pct:.1f}%)" + _offenders_suffix(offenders)
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
    """Targets a live realtime stream instead of a dataset - same
    threshold/offender shape as the dataset evaluators above, reading the
    realtime_cache snapshot (populated by the Flink windowed-aggregation
    job) instead of querying a Dataset file.

    Metric values here reflect only the last 5-second Flink window, not a
    cumulative session total the way they did pre-Redpanda (see
    streaming/metrics.py's module docstring) - thresholds on absolute-sum
    metrics like throughput set before this pipeline shipped will need
    re-tuning."""
    from app.services import realtime_cache

    cfg = rule.config
    stream_id = cfg.get("stream_id", "")
    metric_name = cfg.get("metric_name", "")
    comparison = cfg.get("comparison", "gt")  # "gt" | "lt"
    threshold = float(cfg.get("threshold", 0))

    snap = realtime_cache.get_snapshot(uuid.UUID(stream_id)) if stream_id else None
    if snap is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "Stream not running — nothing to evaluate.",
        )

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


async def _eval_automl_risk(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule, dataset: Dataset,
) -> AlertTrigger:
    """Scores a dataset against an already-trained AutoML run's persisted
    pipeline and flags rows above/below a threshold - the batch counterpart
    to eval_stream_metric's live-stream case. Needs an async AutoMLRun
    lookup unlike the other dataset evaluators (which are pure/sync), which
    is why this isn't dispatched through _DATASET_EVALUATORS - see
    evaluate_rule()."""
    import joblib

    from app.models.automl_run import AutoMLRun

    cfg = rule.config
    run_id = str(cfg.get("run_id") or "").strip()
    threshold = float(cfg.get("threshold", 0.5))
    comparison = cfg.get("comparison", "gt")
    offender_col = str(cfg.get("offender_col") or "").strip()

    if not run_id:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, "No AutoML run configured.")

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Invalid run_id: {run_id}")

    run = (
        await db.execute(select(AutoMLRun).where(AutoMLRun.id == run_uuid, AutoMLRun.org_id == org_id))
    ).scalar_one_or_none()
    if run is None or run.status != "success" or not run.model_path:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "AutoML run not found or has no trained model.",
        )

    if offender_col:
        col_warn = _missing_columns(dataset, {"offender": offender_col})
        if col_warn:
            return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, col_warn)

    try:
        df = ds_service.dataframe_for(dataset)
        pipe = joblib.load(run.model_path)
    except Exception as exc:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Error loading run: {exc}")

    meta = run.meta or {}
    feat_cols = [c for c in (meta.get("feature_columns") or []) if c in df.columns]
    if not feat_cols:
        # Template runs don't populate meta["feature_columns"] - fall back
        # to "every column except the target", the same fallback
        # automl_engine.predict_with_model() already uses for this case.
        target_col = run.target_column
        if target_col and target_col in df.columns:
            feat_cols = [c for c in df.columns if c != target_col]
        else:
            feat_cols = list(df.columns)

    X = df[feat_cols].copy() if feat_cols else df.copy()

    try:
        task = str(meta.get("task") or run.task or "").strip().lower()
        if task == "classification" and hasattr(pipe, "predict_proba"):
            proba = pipe.predict_proba(X)
            scores = [float(row[1]) if len(row) == 2 else float(max(row)) for row in proba]
        else:
            scores = [float(v) for v in pipe.predict(X)]
    except Exception as exc:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Error scoring dataset: {exc}")

    is_high = comparison != "lt"

    if offender_col and offender_col in df.columns:
        offender_values = df[offender_col].astype(str).tolist()
        offenders = sorted({o for o, s in zip(offender_values, scores) if (s > threshold if is_high else s < threshold)})
        value = max(scores) if is_high else min(scores)
    else:
        value = max(scores) if is_high else min(scores)
        offenders = ["All"] if (value > threshold if is_high else value < threshold) else []

    triggered = bool(offenders)
    op_word = "above" if is_high else "below"
    message = f"Predicted risk {value:.3f} {op_word} threshold {threshold:.3f}" + _offenders_suffix(offenders)
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(value, 4), threshold,
        message, offenders,
    )


async def eval_cbm_anomaly(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule) -> AlertTrigger:
    """Fires when a machine has any unacknowledged SensorAnomaly in the
    trailing window (default 24h, config['window_hours'])."""
    from app.models.sensor import SensorAnomaly

    cfg = rule.config
    machine = cfg.get("machine", "")
    window_hours = float(cfg.get("window_hours", 24))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    query = select(SensorAnomaly).where(
        SensorAnomaly.org_id == org_id, SensorAnomaly.detected_at >= cutoff,
        SensorAnomaly.acknowledged_at.is_(None),
    )
    if machine:
        query = query.where(SensorAnomaly.machine == machine)
    anomalies = (await db.execute(query)).scalars().all()

    triggered = len(anomalies) > 0
    machines = sorted({a.machine for a in anomalies})
    message = (
        f"{len(anomalies)} unacknowledged sensor anomal{'y' if len(anomalies) == 1 else 'ies'}"
        f" in the last {window_hours:g}h" if triggered
        else "No unacknowledged sensor anomalies."
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, float(len(anomalies)), 0.0,
        message, machines,
    )


async def eval_cbm_rul(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule) -> AlertTrigger:
    """Fires when a machine's 30-day failure probability crosses config['threshold']
    (default 0.65, matching the app-wide "high risk" convention)."""
    from app.services.rul import days_since_last_breakdown, failure_probability_30d, get_or_refit

    cfg = rule.config
    machine = cfg.get("machine", "")
    threshold = float(cfg.get("threshold", 0.65))

    if not machine:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, "No machine configured.")

    fit = await get_or_refit(db, org_id, machine)
    age = await days_since_last_breakdown(db, org_id, machine)
    prob = failure_probability_30d(fit, age)
    if prob is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            f"Insufficient failure history for {machine} to estimate RUL.",
        )

    triggered = prob >= threshold
    message = (
        f"{machine}: {prob * 100:.1f}% 30-day failure probability (threshold {threshold * 100:.0f}%)"
        if triggered else f"{machine}: {prob * 100:.1f}% 30-day failure probability (within threshold)"
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(prob, 4), threshold,
        message, [machine] if triggered else [],
    )


# ─── Andon / relay output ───────────────────────────────────────────────────

async def _fire_webhook(rule: AlertRule, trigger: AlertTrigger) -> None:
    """POST the live trigger state to an output URL the rule optionally
    configures (config['webhook_url']) - e.g. a relay/light-tower controller.
    Fires the CURRENT triggered state on every scan rather than only on the
    off->on transition: this module never persists prior scan results (see
    module docstring), so there's no "last state" to diff against, and a
    physical andon just needs to mirror whatever the latest scan says - off
    when the next scan clears, on while it doesn't.
    ponytail: no retry/backoff/queue - a relay that misses one poll picks up
    the next scan; add delivery guarantees only if that's proven insufficient."""
    url = (rule.config or {}).get("webhook_url")
    if not url or not str(url).startswith(("http://", "https://")):
        return
    payload = {
        "rule_id": trigger.rule_id, "rule_name": trigger.rule_name, "type": trigger.type,
        "severity": trigger.severity, "triggered": trigger.triggered, "value": trigger.value,
        "threshold": trigger.threshold, "message": trigger.message, "evaluated_at": trigger.evaluated_at,
    }
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
            await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Alert webhook failed for rule %s (%s): %s", rule.id, url, exc)


async def _send_push(db: AsyncSession, org_id: uuid.UUID, trigger: AlertTrigger) -> None:
    """Data-minimal push to every device registered for this org: title/body
    carry no business content, only trigger.rule_id - the app fetches the
    real message directly from this server after the user taps, so alert
    content never transits Apple/Google/Expo's relay (see the mobile design
    spec's "Connecting to a deployment" / data-minimal push sections - never
    hosting customer data extends to never routing it through a push relay).
    One batched HTTP call per scan (Expo's API accepts a list of messages),
    not one call per registered device.
    ponytail: no retry/backoff/queue - same accepted-risk shape as
    _fire_webhook; add if proven insufficient."""
    tokens = (
        await db.execute(select(DeviceToken.expo_push_token).where(DeviceToken.org_id == org_id))
    ).scalars().all()
    if not tokens:
        return
    messages = [
        {
            "to": expo_token, "title": "Evowerk Alert",
            "body": "A rule triggered - open the app for details.",
            "data": {"rule_id": trigger.rule_id},
        }
        for expo_token in tokens
    ]
    try:
        async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT) as client:
            await client.post(_EXPO_PUSH_URL, json=messages)
    except httpx.HTTPError as exc:
        logger.warning("Push notification failed for org %s: %s", org_id, exc)


_DATASET_EVALUATORS = {
    "machine_downtime": _eval_machine_downtime,
    "quality": _eval_quality,
    "scrap": _eval_scrap,
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
    if rule.type == "automl_risk":
        if rule.dataset_id is None:
            return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, "No dataset configured.")
        if dataset is None:
            dataset = await ds_service.get_dataset_or_404(db, org_id, rule.dataset_id)
        return await _eval_automl_risk(db, org_id, rule, dataset)
    if rule.type == "cbm_anomaly":
        return await eval_cbm_anomaly(db, org_id, rule)
    if rule.type == "cbm_rul":
        return await eval_cbm_rul(db, org_id, rule)
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
    rule_by_id = {str(r.id): r for r in rules}

    # Group dataset-backed rules by dataset so N rules sharing one dataset
    # read its file from disk once instead of N times.
    by_dataset: dict[uuid.UUID, list[AlertRule]] = {}
    solo: list[AlertRule] = []
    for rule in rules:
        if rule.type not in ("stream_metric", "cbm_anomaly", "cbm_rul") and rule.dataset_id is not None:
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

    # scan_all only - not scan_one, which backs the rule editor's interactive
    # "Test" button and would otherwise spam an external relay on every keystroke.
    # Push fires only on the off->on transition (rule.last_triggered tracks each
    # rule's state across scans) - unlike the webhook, which mirrors live state
    # every scan per _fire_webhook's own docstring, a push must not repeat every
    # poll interval while a rule stays triggered (that turned a mobile client
    # polling this endpoint every 60s into a notification-spam loop).
    for trigger in results:
        rule = rule_by_id.get(trigger.rule_id)
        if rule is not None:
            await _fire_webhook(rule, trigger)
            if trigger.triggered and not rule.last_triggered:
                await _send_push(db, org_id, trigger)
            if rule.last_triggered != trigger.triggered:
                rule.last_triggered = trigger.triggered
    await db.commit()

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
