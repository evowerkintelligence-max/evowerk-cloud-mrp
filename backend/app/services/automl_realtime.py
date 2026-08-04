"""Live scoring, drift monitoring, and drift-triggered retraining for AutoML
runs bound to a SyncSchedule (app.models.automl_live_binding.AutoMLLiveBinding).

Runs as one step of app.services.sync_scheduler's post-sync pipeline instead
of its own polling thread - the old Redpanda-tick-driven version scored on a
fixed 30s timer against an in-memory raw-row buffer (realtime_cache); this
version scores once per scheduled sync, against the SyncRow rows that sync
just landed, and merges the score directly into that sync's SyncTick.metrics
so the existing stream_metric alerting (app.services.alerts.eval_stream_metric)
still just reads a schedule's latest tick - no new alerting subsystem needed.
See docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.

Drift-checking and drift-triggered retraining are unchanged in substance:
compares the recent SyncRow window against the run's persisted training
baseline (explain_X/explain_y) on drift_check_interval_minutes, and - if
auto_retrain_on_drift and drift reaches drift_severity_trigger - materializes
the buffer into a Dataset and re-runs execute_automl_run, producing a new
versioned AutoMLRun + AutoMLLiveBinding rather than overwriting the old ones.
The new binding is picked up automatically on the schedule's next sync tick
(sync_scheduler looks up the enabled binding by schedule_id each run) - no
separate scheduler registry to re-adopt it into, unlike the old thread-based
version.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.automl_run import AutoMLRun
from app.models.sync_tick import SyncRow, SyncTick
from app.services import automl_engine, automl_storage
from app.services import datasets as ds_service

logger = logging.getLogger(__name__)

_WINDOW_ROWS = 50
_SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2}


def _severity_at_least(severity: str, trigger: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(trigger, 1)


async def score_and_check_binding(db: AsyncSession, binding_id: uuid.UUID, tick: SyncTick) -> None:
    """One post-sync step for a single binding: score the batch that just
    landed in `tick`, merge the score into tick.metrics, and drift-check/
    retrain if due. Never raises - a bad binding/run shouldn't fail the sync
    it's riding on."""
    try:
        await _tick(db, binding_id, tick)
    except Exception:
        logger.exception("automl_realtime scoring failed for binding %s", binding_id)


async def _tick(db: AsyncSession, binding_id: uuid.UUID, tick: SyncTick) -> None:
    binding = (
        await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))
    ).scalar_one_or_none()
    if binding is None or not binding.enabled:
        return

    run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == binding.run_id))).scalar_one_or_none()
    if run is None or run.status != "success" or not run.model_path:
        return

    rows = await _recent_rows(db, binding.schedule_id, _WINDOW_ROWS)
    if not rows:
        return
    df = pd.DataFrame(rows)

    try:
        pipe = joblib.load(run.model_path)
    except Exception:
        logger.exception("Could not load model for run %s", run.id)
        return

    meta = run.meta or {}
    score = _score_latest(pipe, meta, df)
    if score is not None:
        tick.metrics = {**tick.metrics, binding.metric_name: score}
        binding.last_scored_at = datetime.now(timezone.utc)

    due = binding.last_drift_check_at is None or (
        datetime.now(timezone.utc) - binding.last_drift_check_at
        >= timedelta(minutes=binding.drift_check_interval_minutes)
    )
    if due:
        await _check_drift(db, binding, run, df)


async def _recent_rows(db: AsyncSession, schedule_id: uuid.UUID, n: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(SyncRow.row).where(SyncRow.schedule_id == schedule_id)
            .order_by(SyncRow.ts.desc()).limit(n)
        )
    ).scalars().all()
    return list(reversed(rows))  # oldest first


def _score_latest(pipe: Any, meta: dict, df: pd.DataFrame) -> float | None:
    try:
        result = automl_engine.predict_with_model(pipe, meta, df, max_preview_rows=len(df))
    except Exception:
        logger.exception("Live scoring failed")
        return None
    preview = result.get("preview") or []
    if not preview:
        return None
    value = preview[-1].get("prediction")  # buffer order is chronological - last = most recent
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _check_drift(db: AsyncSession, binding: AutoMLLiveBinding, run: AutoMLRun, df: pd.DataFrame) -> None:
    run_dir = automl_storage.run_dir(run.org_id, run.id)
    x_path = run_dir / "explain_X.csv"
    if not x_path.exists():
        return
    explain_X = pd.read_csv(x_path)

    try:
        drift = automl_engine.check_data_drift(run.meta or {}, explain_X, df)
    except ValueError:
        return  # explain sample too small / unavailable - nothing to compare against yet

    binding.last_drift_check_at = datetime.now(timezone.utc)
    severity = drift["overall_severity"]
    binding.last_drift_status = "ok" if severity == "low" else "drifted"

    if binding.auto_retrain_on_drift and _severity_at_least(severity, binding.drift_severity_trigger):
        await _retrain_on_drift(db, binding, run)


async def _retrain_on_drift(db: AsyncSession, binding: AutoMLLiveBinding, run: AutoMLRun) -> None:
    from app.services.automl_runner import execute_automl_run

    rows = await _recent_rows(db, binding.schedule_id, 10_000)
    if len(rows) < 20:
        return  # not enough accumulated data yet to retrain on

    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    dataset = await ds_service.create_dataset(
        db, org_id=binding.org_id, created_by=run.created_by,
        name=f"{run.name} (live retrain {stamp})", filename="live_retrain.csv", data=csv_bytes,
    )
    await db.flush()

    new_run = AutoMLRun(
        org_id=binding.org_id, dataset_id=dataset.id, created_by=run.created_by,
        name=f"{run.name} (retrained {stamp})",
        task=run.task, target_column=run.target_column, feature_columns=run.feature_columns,
        template_id=run.template_id, config=run.config or {}, source_stream_id=binding.schedule_id,
        status="queued",
    )
    db.add(new_run)
    await db.flush()

    new_binding = AutoMLLiveBinding(
        org_id=binding.org_id, run_id=new_run.id, schedule_id=binding.schedule_id,
        metric_name=binding.metric_name, drift_severity_trigger=binding.drift_severity_trigger,
        drift_check_interval_minutes=binding.drift_check_interval_minutes,
        auto_retrain_on_drift=binding.auto_retrain_on_drift, enabled=True,
    )
    db.add(new_binding)
    # The old binding stops scoring once the new run takes over - a versioned
    # successor, not an in-place overwrite (per spec). The new binding is
    # picked up automatically on this schedule's next sync tick.
    binding.enabled = False
    await db.flush()

    # execute_automl_run opens its own DB session to look up new_run by id,
    # so it needs this transaction committed first - this finalizes the
    # whole sync transaction (tick/rows/cursor included), which is fine:
    # everything up to this point is legitimately ready to persist.
    await db.commit()
    await execute_automl_run(str(new_run.id), str(binding.org_id))
