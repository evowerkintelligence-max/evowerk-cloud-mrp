"""Live-polling collector engine, ported from the desktop app's
realtime/engine.py. A "stream" polls one Integration's connector on an
interval, rolls results into a bounded in-memory buffer, computes derived
manufacturing metrics (throughput/scrap/OEE/downtime) over that buffer, and
persists each tick to a JSONL history file.

Kept as an in-process background-thread registry, same as the desktop
version (the connectors are all blocking sync I/O, so a plain thread is the
right primitive - matches app.services.integrations' asyncio.to_thread usage
for the same reason). What's different for a server that can restart:
RealtimeStream (app.models.realtime) persists each stream's definition and
`desired_status`; resume_all_streams() is called from app.main's startup
hook to re-spin any stream still marked "running" after a restart, so a
redeploy doesn't silently and permanently lose it. The live snapshot/status
this module tracks is still ephemeral and only reflects this process.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from kafka import KafkaProducer
from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config
from streaming.metrics import compute_window_metrics
from streaming.schemas import TICKS_TOPIC, Tick

logger = logging.getLogger(__name__)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _history_dir() -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    root = root if root.is_absolute() else (Path.cwd() / root).resolve()
    d = root / "realtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path(stream_id: str) -> Path:
    # stream_id is a RealtimeStream UUID - safe as a bare filename.
    return _history_dir() / f"{stream_id}.jsonl"


def read_history(stream_id: str, max_points: int = 2000) -> list[dict[str, Any]]:
    """Bounded read of a stream's persisted tick history — survives a backend
    restart, unlike the in-memory deque. Returns the most recent `max_points`
    ticks (oldest first)."""
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


class TickProducer:
    """Polls one Integration's connector on an interval and publishes each
    row to Redpanda's realtime.ticks topic, partitioned by stream_id. This
    replaces StreamRunner's old in-memory buffer + metric computation - both
    now live downstream, in the Flink job (streaming/realtime_metrics_job.py)
    and the cache consumer (app.services.realtime_cache) - whenever Redpanda
    is actually reachable. When it isn't (see _record_local/local_snapshot),
    this falls back to computing the same metrics locally, in-process, same
    as StreamRunner used to; that fallback only ever engages on a deployment
    with no Kafka/Flink, so the Redpanda-backed path above is unaffected.
    Still a plain background thread, same as the old StreamRunner: the
    connectors are all blocking sync I/O, so a thread (not asyncio) is the
    right primitive here, and kafka-python's synchronous KafkaProducer
    matches that."""

    def __init__(
        self,
        stream_id: str,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        accumulate: bool,
    ):
        self.stream_id = stream_id
        self.name = name
        self.integration_type = integration_type
        self.integration_config = integration_config
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        # Vestigial: the old StreamRunner used this to decide whether metrics
        # accumulated over a growing in-memory buffer vs. a single poll.
        # Metrics are now always computed over the Flink job's fixed 5s
        # window (see streaming/realtime_metrics_job.py) - this flag has no
        # effect on that. Kept only because it's still a column on
        # RealtimeStream and a request field; not read anywhere downstream.
        self.accumulate = accumulate
        self.status = "running"
        self.error: str | None = None

        # No network I/O here - KafkaProducer construction (and its ~30s
        # bootstrap timeout against an unreachable broker) happens in _run(),
        # on the background thread, so __init__ (called from the request
        # thread handling POST /realtime/streams) always returns immediately.
        self._producer: KafkaProducer | None = None
        self._stop = Event()

        # Local-fallback state - populated only when Redpanda turns out to be
        # unreachable (see _run/_record_local below). Deployments that don't
        # provision Kafka/Flink (e.g. a plain backend+DB host) would otherwise
        # see every stream - any connector type - permanently erroring with
        # nothing ever computed, since there'd be no Flink job or
        # realtime_cache consumer to produce a snapshot either way. This
        # re-derives the same formulas locally (streaming.metrics -
        # "ported unchanged from the old StreamRunner.snapshot()") so the
        # stream still reports live metrics without that infra. Once Kafka IS
        # reachable, self._producer is set and this path is never touched -
        # existing Redpanda/Flink-backed behavior is unchanged.
        self._local_lock = Lock()
        self._local_snapshot: dict[str, Any] | None = None
        self._rows_collected = 0
        self._started_at: datetime | None = None
        self._last_tick_at: datetime | None = None

        self._thread = Thread(target=self._run, daemon=True, name=f"tick-producer-{stream_id[:8]}")
        self._thread.start()

    def _run(self) -> None:
        try:
            conn = get_connector(self.integration_type, self.integration_config)
        except Exception as exc:
            self.status = "error"
            self.error = f"Connector init failed: {exc}"
            return

        settings = get_settings()
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=settings.redpanda_brokers,
                value_serializer=lambda v: v,
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            )
        except Exception as exc:
            logger.warning(
                "TickProducer %s: Redpanda unreachable (%s) - falling back to "
                "local metric computation (no Flink/cross-replica pipeline)",
                self.stream_id, exc,
            )

        # stop() may have already run (and given up on us at its 5s join
        # timeout) while the KafkaProducer constructor above was still
        # blocked bootstrapping. If so, self._producer would otherwise leak
        # (background I/O threads + open socket, never closed) since the
        # while loop below would never run to reach its own cleanup.
        if self._stop.is_set():
            if self._producer is not None:
                self._producer.close(timeout=5)
            return

        while not self._stop.is_set():
            try:
                rows: list[dict] = conn.fetch_all()
                self.error = None
                if self.status == "error":
                    self.status = "running"
                now_dt = datetime.now(timezone.utc)
                now = now_dt.isoformat()
                if self._producer is not None:
                    for row in rows[-1_000:]:
                        tick = Tick(
                            stream_id=self.stream_id,
                            org_id="",  # filled in by callers that have org context; not needed downstream today
                            column_map=self.column_map,
                            row=row,
                            status=self.status,
                            error=self.error,
                            collected_at=now,
                        )
                        self._producer.send(TICKS_TOPIC, key=self.stream_id, value=tick.to_json())
                    self._producer.flush()
                else:
                    self._record_local(rows, now_dt)
            except Exception as exc:
                self.error = str(exc)
                self.status = "error"

            if self.integration_type != "mqtt":
                self._stop.wait(timeout=self.interval_seconds)

    def _record_local(self, rows: list[dict[str, Any]], now: datetime) -> None:
        """Local stand-in for the Flink job's MetricsWindowFunction (see
        streaming/realtime_metrics_job.py) - same compute_window_metrics
        formulas, one "window" per poll rather than a fixed 5s tumbling
        window, since there's no stream processor here to run one."""
        with self._local_lock:
            if self._started_at is None:
                self._started_at = now
            self._last_tick_at = now
            self._rows_collected += len(rows)
            metrics = compute_window_metrics(rows, self.column_map)
            duration_s = int((now - self._started_at).total_seconds())
            h, rem = divmod(max(0, duration_s), 3600)
            m, s = divmod(rem, 60)
            self._local_snapshot = {
                "ts": now.isoformat(), "stream_id": self.stream_id, "name": self.name,
                "status": self.status, "error": self.error,
                "rows_in_window": len(rows), "rows_collected": self._rows_collected,
                "duration": f"{h:02d}:{m:02d}:{s:02d}", "duration_s": duration_s,
                "last_tick_s": 0.0, "metrics": metrics,
            }
        try:
            with history_path(self.stream_id).open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": now.isoformat(), "metrics": metrics}) + "\n")
        except Exception:
            pass  # history is a nice-to-have, same posture as realtime_cache._append_history

    def local_snapshot(self) -> dict[str, Any] | None:
        """None when Redpanda is reachable (the Kafka/Flink/realtime_cache
        pipeline is the source of truth then) or before this runner's first
        successful poll; otherwise the latest locally-computed snapshot, with
        last_tick_s recomputed live rather than frozen at write time."""
        if self._producer is not None:
            return None
        with self._local_lock:
            if self._local_snapshot is None:
                return None
            snap = dict(self._local_snapshot)
        if self._last_tick_at is not None:
            snap["last_tick_s"] = round((datetime.now(timezone.utc) - self._last_tick_at).total_seconds(), 1)
        return snap

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._producer is not None:
            self._producer.close(timeout=5)


# ── Global registry (this process only - see module docstring) ──────────────

_runners: dict[str, TickProducer] = {}
_registry_lock = Lock()


def start_runner(
    *,
    stream_id: uuid.UUID,
    name: str,
    integration_type: str,
    integration_config: dict[str, Any],
    column_map: dict[str, str],
    interval_seconds: int,
    accumulate: bool,
) -> TickProducer:
    """(Re)start the in-memory collector for an existing stream_id. Stopping
    any prior runner for the same id first makes this idempotent, needed both
    for an explicit "start" call on an already-defined stream and for
    resume_all_streams() re-adopting a stream_id that never actually died in
    the registry (defensive; shouldn't happen in practice)."""
    sid = str(stream_id)
    with _registry_lock:
        existing = _runners.pop(sid, None)
    if existing is not None:
        existing.stop()

    runner = TickProducer(
        stream_id=sid,
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
    )
    with _registry_lock:
        _runners[sid] = runner
    return runner


def get_runner(stream_id: uuid.UUID) -> TickProducer | None:
    return _runners.get(str(stream_id))


def stop_runner(stream_id: uuid.UUID) -> bool:
    sid = str(stream_id)
    with _registry_lock:
        runner = _runners.pop(sid, None)
    if runner is None:
        return False
    runner.stop()
    return True


def stop_all_runners() -> None:
    """Called from app.main's shutdown hook so background threads don't get
    silently orphaned when the event loop exits."""
    with _registry_lock:
        runners = list(_runners.values())
        _runners.clear()
    for r in runners:
        r.stop()


async def resume_all_streams() -> None:
    """Called from app.main's startup hook. Re-spins the in-memory collector
    for every RealtimeStream still marked desired_status="running" across all
    orgs - a redeploy/restart stops collection, but the definition survives
    and picks back up here rather than requiring the user to notice and
    restart it manually."""
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
                name=stream.name,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others or app startup
