"""APScheduler-driven periodic pull-sync against a SyncSchedule's Integration
connector - replaces app.services.realtime's thread-based TickProducer +
Redpanda/Flink pipeline. See
docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.

One APScheduler job per SyncSchedule. Each tick: fetch only new rows via the
connector's fetch_since(), compute OEE metrics over that batch, persist a
SyncTick (metrics history) + SyncRow entries (raw-row buffer, capped), and
advance the schedule's cursor. Never raises out of run_sync - a bad
connector/config shouldn't kill the scheduler, same contract the old
TickProducer._run's fetch loop had."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from app.db import session as db_session
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick
from app.services import metrics_calc
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config

logger = logging.getLogger(__name__)

# Raw rows kept per schedule for AutoML live scoring/drift-check - mirrors the
# old realtime_cache._RAW_ROW_BUFFER size.
_ROW_BUFFER_CAP = 10_000

_scheduler: AsyncIOScheduler | None = None


def _job_id(schedule_id: uuid.UUID) -> str:
    return f"sync-schedule-{schedule_id}"


async def start_scheduler() -> None:
    """Called from app.main's startup lifespan hook. Starts the process-wide
    scheduler and registers a job for every schedule still marked
    desired_status='running' - the scheduled-sync equivalent of the old
    realtime.resume_all_streams()."""
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.start()

    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(select(SyncSchedule).where(SyncSchedule.desired_status == "running"))
        ).scalars().all()
    for schedule in rows:
        add_job(schedule.id, schedule.interval_seconds)


async def stop_scheduler() -> None:
    """Called from app.main's shutdown lifespan hook."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_job(schedule_id: uuid.UUID, interval_seconds: int) -> None:
    """(Re)register a schedule's job. Also used as "reschedule" (interval
    change) via replace_existing=True - no separate reschedule primitive
    needed. A no-op if the scheduler hasn't started yet (e.g. called from a
    route before app startup finishes, which shouldn't happen in practice but
    shouldn't crash the request if it does)."""
    if _scheduler is None:
        return
    _scheduler.add_job(
        run_sync, trigger=IntervalTrigger(seconds=max(60, interval_seconds)),
        id=_job_id(schedule_id), args=[schedule_id], replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run once immediately, not just after the first interval
    )


def remove_job(schedule_id: uuid.UUID) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(schedule_id))
    except Exception:
        pass  # job may already be gone (never started, or already removed) - stop/delete are idempotent


def reschedule_job(schedule_id: uuid.UUID, interval_seconds: int) -> None:
    add_job(schedule_id, interval_seconds)


async def run_sync(schedule_id: uuid.UUID) -> None:
    async with db_session.async_session_factory() as db:
        try:
            await _run_sync(db, schedule_id)
        except Exception as exc:
            logger.exception("sync_scheduler run failed for schedule %s", schedule_id)
            schedule = (
                await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
            ).scalar_one_or_none()
            if schedule is not None:
                schedule.last_error = str(exc)
                await db.commit()


async def _run_sync(db, schedule_id: uuid.UUID) -> None:
    schedule = (
        await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
    ).scalar_one_or_none()
    if schedule is None or schedule.desired_status != "running":
        return

    integration = (
        await db.execute(select(Integration).where(Integration.id == schedule.integration_id))
    ).scalar_one_or_none()
    if integration is None:
        schedule.last_error = "Integration not found"
        await db.commit()
        return

    connector = get_connector(integration.type, decrypt_config(integration.config))
    if not hasattr(connector, "fetch_since"):
        schedule.last_error = f"Connector type {integration.type!r} does not support incremental sync"
        await db.commit()
        return

    rows, new_cursor = await asyncio.to_thread(
        connector.fetch_since, schedule.cursor_column, schedule.cursor_value, 500_000
    )
    schedule.last_error = None

    if not rows:
        await db.commit()
        return

    metrics = metrics_calc.compute_window_metrics(rows, schedule.column_map)
    tick = SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=len(rows), metrics=metrics)
    db.add(tick)

    # Each row within this batch gets a strictly increasing ts (not one
    # shared `now`) so "most recent N" ordering (used by _trim_row_buffer and
    # by automl_realtime._recent_rows) reflects fetch order - fetch_since
    # already returns rows oldest-cursor-first, so index order IS recency
    # order. A single shared timestamp would make ties within a batch
    # resolve arbitrarily instead of by that order.
    now = datetime.now(timezone.utc)
    for i, row in enumerate(rows):
        db.add(SyncRow(schedule_id=schedule.id, ts=now + timedelta(microseconds=i), row=row))
    await db.flush()
    await _trim_row_buffer(db, schedule.id)

    schedule.cursor_value = new_cursor
    await db.commit()


async def _trim_row_buffer(db, schedule_id: uuid.UUID) -> None:
    keep_ids = (
        await db.execute(
            select(SyncRow.id).where(SyncRow.schedule_id == schedule_id)
            .order_by(SyncRow.ts.desc()).limit(_ROW_BUFFER_CAP)
        )
    ).scalars().all()
    await db.execute(
        delete(SyncRow).where(SyncRow.schedule_id == schedule_id, SyncRow.id.notin_(keep_ids))
    )
