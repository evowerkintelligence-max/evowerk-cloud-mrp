"""Realtime collection: one asyncio StreamCollector task per stream polls
the Integration's connector, dedups against the previous poll, computes
window metrics, and persists one RealtimeSample row per poll window.

Replaces the old broker pipeline (tick producer -> broker topics -> windowed
job -> per-replica cache consumer): samples land straight in Postgres and
every replica reads them from there, so there is no per-process snapshot
state and no message broker to provision.

The blocking connector I/O runs on a per-collector single-thread executor,
NOT asyncio.to_thread: MQTT connectors block continuously (collect_seconds
per fetch, no interval wait) and would starve the shared default executor
that app.services.integrations also uses.

RealtimeStream (app.models.realtime) persists each stream's definition and
desired_status; resume_all_streams() re-spins collectors on startup, so a
redeploy doesn't silently lose a running stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream, RealtimeStreamMetric
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config
from app.services.sensor_ingest import ingest_rows
from app.services.stream_metrics import compute_window_metrics

logger = logging.getLogger(__name__)

# Per-window write budget: at most this many new rows are written into one
# sample. Rows beyond it are NOT marked seen, so they stay new and drain into
# the following windows (see _poll_once), oldest first.
_MAX_ROWS_PER_WINDOW = 1_000
_PRUNE_EVERY_S = 3600.0
_last_prune = 0.0


# ── Ingest dedup ─────────────────────────────────────────────────────────────

def _row_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def new_rows_since_last_poll(
    rows: list[dict[str, Any]], prev: Counter
) -> tuple[list[dict[str, Any]], Counter]:
    """Multiset difference of this poll against the previous one. Most
    connectors' fetch_all() returns the whole table every poll; only the
    rows that were not in the last poll are new. Multiset (not set)
    semantics keep genuinely duplicate rows within one poll.

    ponytail: two identical rows in ADJACENT polls with no distinguishing
    column are treated as re-fetched, and a process restart resets prev so
    the whole table re-ingests, in _MAX_ROWS_PER_WINDOW-sized windows.
    Upgrade path: persist a per-stream high-water column or row-hash set if
    either bites.
    """
    keys = [_row_key(r) for r in rows]
    cur = Counter(keys)
    remaining = dict(cur - prev)
    out: list[dict[str, Any]] = []
    # Walk in reverse: for append-only sources the new rows are at the end.
    for row, key in zip(reversed(rows), reversed(keys)):
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            out.append(row)
    out.reverse()
    return out, cur


# ── Collector ────────────────────────────────────────────────────────────────

class StreamCollector:
    """Polls one Integration's connector on an interval and writes one
    RealtimeSample per window. Construction is passive; start() schedules
    the loop on the running event loop (tests drive _poll_once directly)."""

    def __init__(
        self,
        *,
        stream_id: uuid.UUID,
        org_id: uuid.UUID,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        accumulate: bool,
    ):
        self.stream_id = stream_id
        self.org_id = org_id
        self.name = name
        self.integration_type = integration_type
        self.integration_config = integration_config
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        # Vestigial: still a RealtimeStream column and request field, but
        # metrics are always per-window now. Not read anywhere downstream.
        self.accumulate = accumulate
        self.status = "running"
        self.error: str | None = None

        self._conn: Any = None
        self._prev: Counter = Counter()
        self._rows_collected = 0
        self._duration_base = 0
        self._started = time.monotonic()
        self._last_rows_at: float | None = None
        # ponytail: ThreadPoolExecutor workers are non-daemon and joined at
        # interpreter exit, and shutdown(wait=False) does not cancel a future
        # already running, so a collector cancelled mid-fetch delays process
        # exit until that blocking call returns (an MQTT collect_seconds
        # window, or a TCP read with no timeout against a dead SQL Server).
        # Upgrade path: bound the blocking call itself with per-connector
        # fetch timeouts, not daemon threads (a killed thread mid-fetch would
        # leak the connector's socket).
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"collector-{str(stream_id)[:8]}"
        )
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"collector-{str(self.stream_id)[:8]}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        else:
            self._executor.shutdown(wait=False)

    async def _run(self) -> None:
        try:
            # Seeded BEFORE the connector init so a failing connector's error
            # sample still carries the stream's real accumulated counters
            # instead of resetting them to zero.
            await self._seed_counters()
            try:
                self._conn = get_connector(self.integration_type, self.integration_config)
            except Exception as exc:
                self.status, self.error = "error", f"Connector init failed: {exc}"[:2000]
                await self._write_sample([])  # surface the failure in snapshots
                return
            while True:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("collector %s poll failed", self.stream_id)
                # MQTT blocks for collect_seconds inside fetch_all, so no
                # extra wait, except after an error, where the interval
                # doubles as backoff so a dead broker isn't a busy-loop.
                if self.integration_type != "mqtt" or self.status == "error":
                    await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            self._executor.shutdown(wait=False)

    async def _seed_counters(self) -> None:
        """Carry rows_collected/duration_s over from the stream's latest
        sample so the counters survive restarts."""
        async with db_session.async_session_factory() as db:
            latest = await _latest_sample(db, self.stream_id)
        if latest is not None:
            self._rows_collected = latest.rows_collected
            self._duration_base = latest.duration_s

    def _fetch_and_dedup(self) -> tuple[list[dict[str, Any]], Counter]:
        """Runs on the collector's executor. The dedup rides along with the
        fetch because it json.dumps-hashes every row of the source table, and
        most connectors return the whole table each poll (SQL Server's own
        default ceiling is max_rows=500_000): on the event loop that stalls
        every other request in the worker."""
        return new_rows_since_last_poll(self._conn.fetch_all(), self._prev)

    async def _poll_once(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            new_rows, seen = await loop.run_in_executor(self._executor, self._fetch_and_dedup)
        except Exception as exc:
            # Truncated to the RealtimeSample.error / RealtimeStream.last_error
            # column width (String(2000)): on Postgres an over-length value
            # fails the INSERT, so no sample lands at all and the stream just
            # freezes with no error shown.
            self.status, self.error = "error", str(exc)[:2000]
            new_rows = []  # keep _prev: a transient fetch error must not re-ingest the table
        else:
            self.status, self.error = "running", None
            if len(new_rows) > _MAX_ROWS_PER_WINDOW:
                deferred = new_rows[_MAX_ROWS_PER_WINDOW:]
                new_rows = new_rows[:_MAX_ROWS_PER_WINDOW]  # oldest first: drain in arrival order
                # Adopt `seen` MINUS the deferred rows: they stay new, so the
                # next poll picks them up instead of them being marked seen and
                # lost forever. Adopting nothing at all would instead re-emit
                # this same window's rows on the next poll, as duplicates.
                self._prev = seen - Counter(_row_key(r) for r in deferred)
            else:
                self._prev = seen
        await self._write_sample(new_rows)
        await _maybe_prune()

    async def _write_sample(self, new_rows: list[dict[str, Any]]) -> None:
        now_mono = time.monotonic()
        if new_rows:
            self._last_rows_at = now_mono
        self._rows_collected += len(new_rows)
        sample = RealtimeSample(
            org_id=self.org_id,
            stream_id=self.stream_id,
            status=self.status,
            error=self.error,
            rows_in_window=len(new_rows),
            rows_collected=self._rows_collected,
            duration_s=self._duration_base + int(now_mono - self._started),
            last_tick_s=(
                round(now_mono - self._last_rows_at, 1) if self._last_rows_at is not None else None
            ),
            metrics=compute_window_metrics(new_rows, self.column_map),
            raw_rows=new_rows,
        )
        async with db_session.async_session_factory() as db:
            db.add(sample)
            stream = await db.get(RealtimeStream, self.stream_id)
            if stream is not None:
                if stream.last_error != self.error:
                    stream.last_error = self.error
                if new_rows:
                    await ingest_rows(db, stream, new_rows)
            await db.commit()


async def _maybe_prune() -> None:
    """Global retention sweep, at most hourly per process, fired by whichever
    collector polls first past the hour mark. Global (not per stream) so
    stopped streams' rows are pruned too."""
    global _last_prune
    if time.monotonic() - _last_prune < _PRUNE_EVERY_S:
        return
    _last_prune = time.monotonic()
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().realtime_retention_days)
    async with db_session.async_session_factory() as db:
        await db.execute(delete(RealtimeSample).where(RealtimeSample.ts < cutoff))
        await db.commit()


# ── Reads (routes, alerts, AutoML) ───────────────────────────────────────────

def _fmt_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def sample_dict(sample: RealtimeSample) -> dict[str, Any]:
    """The snapshot envelope frontend/src/pages/Realtime.tsx reads, the same
    keys the old SSE feed carried."""
    return {
        "ts": sample.ts.isoformat() if sample.ts else None,
        "stream_id": str(sample.stream_id),
        "status": sample.status,
        "error": sample.error,
        "rows_in_window": sample.rows_in_window,
        "rows_collected": sample.rows_collected,
        "duration": _fmt_duration(sample.duration_s),
        "duration_s": sample.duration_s,
        "last_tick_s": sample.last_tick_s,
        "metrics": dict(sample.metrics or {}),
    }


async def _latest_sample(db: AsyncSession, stream_id: uuid.UUID) -> RealtimeSample | None:
    return (
        await db.execute(
            select(RealtimeSample)
            .where(RealtimeSample.stream_id == stream_id)
            .order_by(RealtimeSample.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def samples_since(
    db: AsyncSession, stream_id: uuid.UUID, since: int | None, limit: int = 500
) -> tuple[list[RealtimeSample], int | None]:
    """Incremental sync: rows with id > since, oldest first. No `since`
    returns just the latest sample (cheap page-open, no backfill). The
    returned cursor is the highest id RETURNED (not the table max), so a
    truncated page self-heals on the next poll."""
    limit = max(1, min(2000, int(limit)))
    if since is None:
        latest = await _latest_sample(db, stream_id)
        rows = [latest] if latest is not None else []
    else:
        rows = list(
            (
                await db.execute(
                    select(RealtimeSample)
                    .where(RealtimeSample.stream_id == stream_id, RealtimeSample.id > int(since))
                    .order_by(RealtimeSample.id)
                    .limit(limit)
                )
            ).scalars()
        )
    cursor = rows[-1].id if rows else since
    return rows, cursor


async def external_metrics(db: AsyncSession, stream_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id == stream_id))
    ).scalars().all()
    return {m.name: m.value for m in rows}


def _assemble_snapshot(
    stream: RealtimeStream, sample: RealtimeSample | None, external: dict[str, Any]
) -> dict[str, Any]:
    if sample is None:
        snap: dict[str, Any] = {
            "ts": None, "stream_id": str(stream.id), "name": stream.name,
            "status": "stopped" if stream.desired_status == "stopped" else "starting",
            "error": stream.last_error,
            "rows_in_window": 0, "rows_collected": 0,
            "duration": None, "duration_s": None, "last_tick_s": None, "metrics": {},
        }
    else:
        snap = sample_dict(sample)
        snap["name"] = stream.name
        # A stopped stream keeps its real final counters/metrics, but the
        # reported status must reflect the user's stop, not the last write.
        if stream.desired_status == "stopped":
            snap["status"] = "stopped"
    if external:
        snap["metrics"] = {**snap["metrics"], **external}
    snap["interval_seconds"] = stream.interval_seconds
    return snap


async def latest_snapshot(db: AsyncSession, stream: RealtimeStream) -> dict[str, Any]:
    sample = await _latest_sample(db, stream.id)
    external = await external_metrics(db, stream.id)
    return _assemble_snapshot(stream, sample, external)


async def latest_snapshots_bulk(
    db: AsyncSession, streams: list[RealtimeStream]
) -> dict[str, dict[str, Any]]:
    """latest_snapshot for N streams in two queries instead of 2N.
    Portable latest-per-stream (works on the SQLite test DB, no DISTINCT ON)."""
    if not streams:
        return {}
    ids = [s.id for s in streams]
    # max(id) per stream, then fetch those rows.
    max_ids = (
        select(func.max(RealtimeSample.id))
        .where(RealtimeSample.stream_id.in_(ids))
        .group_by(RealtimeSample.stream_id)
    )
    samples = (
        await db.execute(select(RealtimeSample).where(RealtimeSample.id.in_(max_ids)))
    ).scalars().all()
    by_stream = {str(s.stream_id): s for s in samples}
    ext_rows = (
        await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id.in_(ids)))
    ).scalars().all()
    ext_by_stream: dict[str, dict[str, Any]] = {}
    for m in ext_rows:
        ext_by_stream.setdefault(str(m.stream_id), {})[m.name] = m.value
    return {
        str(s.id): _assemble_snapshot(s, by_stream.get(str(s.id)), ext_by_stream.get(str(s.id), {}))
        for s in streams
    }


async def recent_rows(db: AsyncSession, stream_id: uuid.UUID, n: int = 500) -> list[dict[str, Any]]:
    """Last `n` ingested raw rows for a stream, oldest first, flattened
    from the newest non-empty samples. The actual feature columns AutoML
    scores on, not the windowed aggregates."""
    n = max(1, int(n))
    # `n` counts ROWS, but the LIMIT counts SAMPLES, and each sample carries up
    # to _MAX_ROWS_PER_WINDOW (1000) rows that .all() materializes before the
    # loop below can break. So the sample scan is capped independently of `n`.
    #
    # ponytail: 200-sample ceiling, so a caller asking for more rows than the
    # newest 200 samples happen to hold gets fewer rows than it requested
    # (every non-empty sample has >= 1 row, so 200 samples always cover the
    # scoring path's 50 and the retrain path's 20-row guard). Upgrade path:
    # paginate the sample scan, or store raw rows in their own table if
    # per-row querying is ever needed.
    samples = (
        await db.execute(
            select(RealtimeSample.raw_rows)
            .where(RealtimeSample.stream_id == stream_id, RealtimeSample.rows_in_window > 0)
            .order_by(RealtimeSample.id.desc())
            .limit(min(n, 200))
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for raw in samples:  # newest sample first
        out = list(raw) + out
        if len(out) >= n:
            break
    return out[-n:]


async def set_external_metric(db: AsyncSession, stream_id: uuid.UUID, name: str, value: Any) -> None:
    """Upsert one (stream, name) external metric row. Caller commits."""
    await db.merge(
        RealtimeStreamMetric(
            stream_id=stream_id, name=name, value=value, updated_at=datetime.now(timezone.utc)
        )
    )


# ── TEMPORARY JSONL history shims (deleted in the broker-stack removal task;
# realtime_cache.py and the /history route still import these until then) ────

def _history_dir() -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    root = root if root.is_absolute() else (Path.cwd() / root).resolve()
    d = root / "realtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path(stream_id: str) -> Path:
    return _history_dir() / f"{stream_id}.jsonl"


def read_history(stream_id: str, max_points: int = 2000) -> list[dict[str, Any]]:
    path = history_path(stream_id)
    if not path.exists():
        return []
    max_points = max(1, min(20_000, int(max_points)))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max_points:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def delete_history(stream_id: str) -> None:
    try:
        history_path(stream_id).unlink()
    except FileNotFoundError:
        pass


# ── Global registry (this process only) ──────────────────────────────────────

_runners: dict[str, StreamCollector] = {}


def start_runner(
    *,
    stream_id: uuid.UUID,
    org_id: uuid.UUID,
    name: str,
    integration_type: str,
    integration_config: dict[str, Any],
    column_map: dict[str, str],
    interval_seconds: int,
    accumulate: bool,
) -> StreamCollector:
    """(Re)start the collector for an existing stream_id. Idempotent: any
    prior collector for the same id is cancelled without being awaited
    (fire-and-forget; its finally block shuts its executor down). One never
    started has no task to cancel, so its executor is shut down here."""
    sid = str(stream_id)
    existing = _runners.pop(sid, None)
    if existing is not None:
        if existing._task is not None:
            existing._task.cancel()
        else:
            existing._executor.shutdown(wait=False)

    collector = StreamCollector(
        stream_id=stream_id,
        org_id=org_id,
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
    )
    collector.start()
    _runners[sid] = collector
    return collector


def get_runner(stream_id: uuid.UUID) -> StreamCollector | None:
    return _runners.get(str(stream_id))


async def stop_runner(stream_id: uuid.UUID) -> bool:
    runner = _runners.pop(str(stream_id), None)
    if runner is None:
        return False
    await runner.stop()
    return True


async def stop_all_runners() -> None:
    """Called from app.main's shutdown hook so collector tasks don't get
    silently orphaned when the event loop exits."""
    runners = list(_runners.values())
    _runners.clear()
    for r in runners:
        await r.stop()


async def resume_all_streams() -> None:
    """Called from app.main's startup hook. Re-spins the collector for every
    RealtimeStream still marked desired_status="running" across all orgs, so
    a redeploy picks running streams back up."""
    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(
                select(RealtimeStream, Integration)
                .join(Integration, Integration.id == RealtimeStream.integration_id)
                .where(RealtimeStream.desired_status == "running")
            )
        ).all()

    for stream, integration in rows:
        try:
            start_runner(
                stream_id=stream.id,
                org_id=stream.org_id,
                name=stream.name,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others
