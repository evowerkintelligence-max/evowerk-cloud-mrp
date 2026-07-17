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
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services.integrations import decrypt_config
from app.services.realtime_cache import RealtimeCache
from app.services.realtime_producer import TickProducer
from streaming.bus import MessageBus, RedpandaMessageBus

_bus: MessageBus | None = None
_cache: RealtimeCache | None = None
_producers: dict[str, TickProducer] = {}
_loop: asyncio.AbstractEventLoop | None = None


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


def _append_history(stream_id: str, snapshot: dict[str, Any]) -> None:
    # Same posture as the old StreamRunner._append_history: a disk hiccup
    # here must never take down the cache consumer.
    try:
        with history_path(stream_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": snapshot["ts"], "metrics": snapshot["metrics"]}) + "\n")
    except Exception:
        pass


# ── Bus / cache wiring ───────────────────────────────────────────────────────

def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        _bus = RedpandaMessageBus(brokers=get_settings().redpanda_brokers)
    return _bus


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
    global _loop
    if _loop is None:
        _loop = asyncio.get_running_loop()

    sid = str(stream_id)
    existing = _producers.pop(sid, None)
    if existing is not None:
        existing.stop()

    producer = TickProducer(
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
    return _producers.get(str(stream_id))


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
