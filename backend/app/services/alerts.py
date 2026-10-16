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
from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.oee_calc import val_expr  # noqa: F401
from app.models.alert import AlertRule
from app.models.dataset import Dataset
from app.models.device_token import DeviceToken
from app.services import datasets as ds_service
from app.services.alerts_evaluators import (  # noqa: F401
    AlertTrigger, _MAX_OFFENDERS_SHOWN, _eval_automl_risk, _eval_inventory, _eval_machine_downtime,
    _eval_production_target, _eval_quality, _eval_scrap, _missing_columns, _offenders_suffix, _sum2_by_group,
    _sum2_total, _sum_by_group, _sum_total, _time_filter_sql, eval_cbm_anomaly, eval_cbm_rul, eval_stream_metric,
)

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
        return await eval_stream_metric(db, rule)
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


async def stream_alerts(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> list[AlertTrigger]:
    """Enabled stream_metric rules targeting this schedule, evaluated live -
    used by sync_schedules.py's list/snapshot endpoints to attach alarm state."""
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["schedule_id"].as_string() == str(schedule_id),
        )
    )
    return [await eval_stream_metric(db, r) for r in result.scalars().all()]


async def stream_alerts_bulk(
    db: AsyncSession, org_id: uuid.UUID, schedule_ids: list[uuid.UUID],
) -> dict[str, list[AlertTrigger]]:
    """stream_alerts for N schedules in one query - used by sync_schedules.py's
    list_schedules so listing schedules isn't a query per schedule."""
    if not schedule_ids:
        return {}
    sids = [str(s) for s in schedule_ids]
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["schedule_id"].as_string().in_(sids),
        )
    )
    by_schedule: dict[str, list[AlertTrigger]] = {}
    for rule in result.scalars().all():
        by_schedule.setdefault(rule.config.get("schedule_id"), []).append(await eval_stream_metric(db, rule))
    return by_schedule
