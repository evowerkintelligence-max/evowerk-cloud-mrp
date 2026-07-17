"""Live scoring, drift monitoring, and drift-triggered retraining for
AutoML runs bound to a RealtimeStream (app.models.automl_live_binding.
AutoMLLiveBinding).

Same in-process-thread precedent as app.services.realtime's old StreamRunner
(now TickProducer): one lightweight background thread per binding, no new
job queue. Each tick:

1. Scores the stream's recent raw rows (app.services.realtime_cache.
   recent_rows(), sourced from the realtime.ticks Redpanda topic - not the
   Flink job's windowed metrics, which don't carry the raw feature columns a
   trained pipeline needs) against the run's trained pipeline
   (automl_engine.predict_with_model) and publishes the result via
   realtime_cache.set_external_metric() - existing stream_metric alert rules
   (app.services.alerts.eval_stream_metric) already know how to alert on
   whatever shows up in a stream's metrics dict, so nothing new is needed
   there.
2. On `drift_check_interval_minutes` (default daily), compares the same
   recent window against the run's persisted training baseline
   (explain_X/explain_y, via automl_engine.check_data_drift).
3. If `auto_retrain_on_drift` and drift reaches `drift_severity_trigger`,
   materializes the stream's accumulated buffer into a Dataset (same
   overwrite-one-dataset pattern app.services.downtime_tracking's
   DowntimeLiveDataset uses) and re-runs execute_automl_run against it,
   producing a new, versioned AutoMLRun + AutoMLLiveBinding rather than
   overwriting the old ones.

Reads via realtime_cache rather than the old in-process StreamRunner buffer,
so - unlike the original design - scoring/drift now work from *any* backend
replica regardless of which one is actually running the stream's collector
(every replica's CacheConsumer sees every tick, per its module docstring).
The one caveat carried forward: realtime_cache's raw-row buffer is itself
in-memory per replica, so a fresh replica has an empty buffer until ticks
start arriving - a binding scored immediately after a redeploy is a silent
no-op tick until then, not an error, same posture as before.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.automl_run import AutoMLRun
from app.services import automl_engine, automl_storage
from app.services import datasets as ds_service
from app.services import realtime_cache

logger = logging.getLogger(__name__)

# How often a binding's scheduler thread wakes up to (re)score the stream.
# Drift-checking has its own per-binding cadence (drift_check_interval_minutes);
# this is just the scoring tick, kept fixed rather than configurable - no
# customer has asked for sub-30s AutoML scoring and the stream itself is
# already polling on its own interval upstream.
_SCORE_POLL_SECONDS = 30
# Raw rows pulled from the stream's live buffer per tick, for both scoring
# and drift-checking - large enough for lag/rolling features, small enough
# to stay cheap on every tick.
_WINDOW_ROWS = 50
_SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2}


def _severity_at_least(severity: str, trigger: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(trigger, 1)


async def score_and_check_binding(binding_id: uuid.UUID) -> None:
    """One tick of work for a single binding: score, and drift-check/retrain
    if due. Never raises - a bad binding/run/stream shouldn't kill the
    scheduler thread, same contract as StreamRunner._run's fetch loop."""
    async with db_session.async_session_factory() as db:
        try:
            await _tick(db, binding_id)
        except Exception:
            logger.exception("automl_realtime tick failed for binding %s", binding_id)


async def _tick(db: AsyncSession, binding_id: uuid.UUID) -> None:
    binding = (
        await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))
    ).scalar_one_or_none()
    if binding is None or not binding.enabled:
        return

    run = (
        await db.execute(select(AutoMLRun).where(AutoMLRun.id == binding.run_id))
    ).scalar_one_or_none()
    if run is None or run.status != "success" or not run.model_path:
        return

    rows = realtime_cache.recent_rows(binding.stream_id, _WINDOW_ROWS)
    if not rows:
        return  # no ticks buffered for this stream in this replica yet - nothing to score this tick
    df = pd.DataFrame(rows)

    try:
        pipe = joblib.load(run.model_path)
    except Exception:
        logger.exception("Could not load model for run %s", run.id)
        return

    meta = run.meta or {}
    score = _score_latest(pipe, meta, df)
    if score is not None:
        realtime_cache.set_external_metric(binding.stream_id, binding.metric_name, score)
        binding.last_scored_at = datetime.now(timezone.utc)

    due = binding.last_drift_check_at is None or (
        datetime.now(timezone.utc) - binding.last_drift_check_at
        >= timedelta(minutes=binding.drift_check_interval_minutes)
    )
    if due:
        await _check_drift(db, binding, run, df)

    await db.commit()


def _score_latest(pipe: Any, meta: dict, df: pd.DataFrame) -> float | None:
    try:
        result = automl_engine.predict_with_model(pipe, meta, df, max_preview_rows=len(df))
    except Exception:
        logger.exception("Live scoring failed")
        return None
    preview = result.get("preview") or []
    if not preview:
        return None
    value = preview[-1].get("prediction")  # buffer/preview order is chronological - last = most recent
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

    rows = realtime_cache.recent_rows(binding.stream_id, 10_000)
    if len(rows) < 20:
        return  # not enough accumulated stream data yet to retrain on

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
        template_id=run.template_id, config=run.config or {}, source_stream_id=binding.stream_id,
        status="queued",
    )
    db.add(new_run)
    await db.flush()

    new_binding = AutoMLLiveBinding(
        org_id=binding.org_id, run_id=new_run.id, stream_id=binding.stream_id,
        metric_name=binding.metric_name, drift_severity_trigger=binding.drift_severity_trigger,
        drift_check_interval_minutes=binding.drift_check_interval_minutes,
        auto_retrain_on_drift=binding.auto_retrain_on_drift, enabled=True,
    )
    db.add(new_binding)
    # The old binding stops scoring/drift-checking once the new run takes
    # over - a versioned successor, not an in-place overwrite (per spec).
    binding.enabled = False
    await db.commit()
    await db.refresh(new_run)

    await execute_automl_run(str(new_run.id), str(binding.org_id))
    start_binding_scheduler(new_binding.id, binding.org_id)


# ── Scheduling (one background thread per binding, mirrors StreamRunner) ────

class BindingScheduler:
    def __init__(self, binding_id: uuid.UUID, poll_seconds: int = _SCORE_POLL_SECONDS):
        self.binding_id = binding_id
        self._stop = Event()
        self._thread = Thread(
            target=self._run, daemon=True, name=f"automl-live-{str(binding_id)[:8]}", args=(max(5, poll_seconds),)
        )
        self._thread.start()

    def _run(self, poll_seconds: int) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(score_and_check_binding(self.binding_id))
            except Exception:
                logger.exception("automl_realtime scheduler crashed for binding %s", self.binding_id)
            self._stop.wait(timeout=poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


_schedulers: dict[str, BindingScheduler] = {}
_registry_lock = Lock()


def start_binding_scheduler(binding_id: uuid.UUID, org_id: uuid.UUID) -> BindingScheduler:  # noqa: ARG001 - org_id kept for signature symmetry with realtime.start_runner
    bid = str(binding_id)
    with _registry_lock:
        existing = _schedulers.pop(bid, None)
    if existing is not None:
        existing.stop()

    sched = BindingScheduler(binding_id)
    with _registry_lock:
        _schedulers[bid] = sched
    return sched


def get_binding_scheduler(binding_id: uuid.UUID) -> BindingScheduler | None:
    return _schedulers.get(str(binding_id))


def stop_binding_scheduler(binding_id: uuid.UUID) -> bool:
    bid = str(binding_id)
    with _registry_lock:
        sched = _schedulers.pop(bid, None)
    if sched is None:
        return False
    sched.stop()
    return True


def stop_all_binding_schedulers() -> None:
    """Called from app.main's shutdown hook, same as realtime.stop_all_runners."""
    with _registry_lock:
        scheds = list(_schedulers.values())
        _schedulers.clear()
    for s in scheds:
        s.stop()


async def resume_all_bindings() -> None:
    """Called from app.main's startup hook, same as realtime.resume_all_streams:
    re-spins a scheduler thread for every AutoMLLiveBinding still enabled after
    a redeploy/restart wiped the in-memory registry."""
    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.enabled.is_(True)))
        ).scalars().all()

    for binding in rows:
        try:
            start_binding_scheduler(binding.id, binding.org_id)
        except Exception:
            continue  # best-effort resume, same as resume_all_streams
