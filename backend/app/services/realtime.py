"""Live-polling collector engine. Redpanda+Flink slice: this module now owns
only the thin poll->publish producer (TickProducer, app/services/
realtime_producer.py) and the message bus / cache wiring - actual metric
computation moved to the Flink job (backend/streaming/realtime_metrics_job.py)
and the RealtimeCache (app/services/realtime_cache.py). See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md.

RealtimeStream (app.models.realtime) still persists each stream's definition
and desired_status; resume_all_streams() still re-adopts anything marked
"running" after a restart. TickProducer's connector poll loop is still
per-process, same as StreamRunner was - what changed is that snapshot state
(RealtimeCache) is now fed by Redpanda, so any replica consuming
realtime.metrics has it, not just the one running a given stream's producer.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kafka import KafkaProducer
from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services.integrations import decrypt_config
from streaming.schemas import TICKS_TOPIC, Tick


# ── History (unchanged from before this slice) ──────────────────────────────

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


class TickProducer:
    """Polls one Integration's connector on an interval and publishes each
    row to Redpanda's realtime.ticks topic, partitioned by stream_id. This
    replaces StreamRunner's old in-memory buffer + metric computation - both
    now live downstream, in the Flink job (streaming/realtime_metrics_job.py)
    and the cache consumer (app.services.realtime_cache). Still a plain
    background thread, same as the old StreamRunner: the connectors are all
    blocking sync I/O, so a thread (not asyncio) is the right primitive
    here, and kafka-python's synchronous KafkaProducer matches that."""

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
            self.status = "error"
            self.error = f"Redpanda connection failed: {exc}"
            return

        # stop() may have already run (and given up on us at its 5s join
        # timeout) while the KafkaProducer constructor above was still
        # blocked bootstrapping. If so, self._producer would otherwise leak
        # (background I/O threads + open socket, never closed) since the
        # while loop below would never run to reach its own cleanup.
        if self._stop.is_set():
            self._producer.close(timeout=5)
            return

        while not self._stop.is_set():
            try:
                rows: list[dict] = conn.fetch_all()
                self.error = None
                if self.status == "error":
                    self.status = "running"
                now = datetime.now(timezone.utc).isoformat()
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
            except Exception as exc:
                self.error = str(exc)
                self.status = "error"

            if self.integration_type != "mqtt":
                self._stop.wait(timeout=self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._producer is not None:
            self._producer.close(timeout=5)


# ── Bus / cache wiring ───────────────────────────────────────────────────────

_runners: dict[str, TickProducer] = {}
_registry_lock = Lock()


async def start_cache(group_id: str) -> None:
    """Called once from app.main's startup hook. group_id MUST be unique per
    backend replica - see RealtimeCache's docstring."""
    global _cache
    _cache = RealtimeCache(get_bus(), group_id=group_id, on_update=_append_history)
    await _cache.start()


async def stop_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.stop()
        _cache = None


def get_snapshot(stream_id: uuid.UUID) -> dict[str, Any] | None:
    if _cache is None:
        return None
    return _cache.get_snapshot(str(stream_id))


def _publish_sync(topic: str, msg) -> None:
    """Bridges TickProducer's background poll thread into the async bus.
    start_runner() below always captures the loop while running inside a
    live event loop (it's called synchronously from async route handlers),
    so this can safely hop back onto it from any producer's thread."""
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        get_bus().publish(topic, msg.stream_id, msg.to_json()), _loop
    ).result(timeout=5)


# ── Producer registry (replaces the old StreamRunner registry) ──────────────

def start_runner(
    *,
    stream_id: uuid.UUID,
    name: str,
    org_id: uuid.UUID,
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
    existing = _producers.pop(sid, None)
    if existing is not None:
        existing.stop()

    runner = TickProducer(
        stream_id=sid,
        org_id=str(org_id),
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
        publish=_publish_sync,
    )
    _producers[sid] = producer
    return producer


def get_runner(stream_id: uuid.UUID) -> TickProducer | None:
    return _runners.get(str(stream_id))


def stop_runner(stream_id: uuid.UUID) -> bool:
    sid = str(stream_id)
    producer = _producers.pop(sid, None)
    if producer is None:
        return False
    producer.stop()
    return True


def stop_all_runners() -> None:
    producers = list(_producers.values())
    _producers.clear()
    for p in producers:
        p.stop()


async def resume_all_streams() -> None:
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
                org_id=stream.org_id,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others or app startup
