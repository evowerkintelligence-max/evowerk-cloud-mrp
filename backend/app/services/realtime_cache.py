# backend/app/services/realtime_cache.py
"""Asyncio background consumer of the realtime.metrics Redpanda topic - the
replacement for reading a StreamRunner's in-memory state directly. Every
backend replica runs its own CacheConsumer with a unique consumer group, so
every replica sees every stream's updates (a shared group would split
partitions across replicas and reintroduce the exact "not running in this
process" bug this whole pipeline exists to fix). Also persists each snapshot
to the same JSONL history file app.services.realtime's StreamRunner used to
write directly, so stream_history() keeps working unchanged."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from typing import Any

from aiokafka import AIOKafkaConsumer

from app.core.config import get_settings
from app.services.realtime import history_path
from streaming.schemas import METRICS_TOPIC, MetricSnapshot


class CacheConsumer:
    def __init__(self, group_id: str | None = None, brokers: str | None = None):
        self.group_id = group_id or f"realtime-cache-{socket.gethostname()}-{os.getpid()}"
        self.brokers = brokers or get_settings().redpanda_brokers
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            METRICS_TOPIC, bootstrap_servers=self.brokers,
            group_id=self.group_id, auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            try:
                snap = MetricSnapshot.from_json(msg.value)
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
            h, rem = divmod(max(0, snap.duration_s), 3600)
            m, s = divmod(rem, 60)
            snap_dict = {
                "ts": snap.ts, "stream_id": snap.stream_id, "name": snap.name,
                "status": snap.status, "error": snap.error,
                "rows_in_window": snap.rows_in_window, "rows_collected": snap.rows_collected,
                # "duration" (formatted HH:MM:SS) is read directly by frontend/src/pages/Realtime.tsx -
                # StreamRunner.snapshot() used to format it the same way; preserved here so the SSE
                # JSON envelope is unchanged end to end.
                "duration": f"{h:02d}:{m:02d}:{s:02d}",
                "duration_s": snap.duration_s, "last_tick_s": snap.last_tick_s,
                "metrics": snap.metrics,
            }
            self._snapshots[snap.stream_id] = snap_dict
            self._append_history(snap.stream_id, snap_dict)

    def _append_history(self, stream_id: str, snap: dict[str, Any]) -> None:
        try:
            with history_path(stream_id).open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": snap["ts"], "metrics": snap["metrics"]}) + "\n")
        except Exception:
            pass  # history is a nice-to-have, same posture as the old StreamRunner._append_history

    def get_snapshot(self, stream_id: uuid.UUID) -> dict[str, Any] | None:
        return self._snapshots.get(str(stream_id))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer is not None:
            await self._consumer.stop()


# ── Module-level singleton for this process, started/stopped from app.main ──

_instance: CacheConsumer | None = None


async def start_cache_consumer(brokers: str | None = None) -> None:
    global _instance
    _instance = CacheConsumer(brokers=brokers)
    await _instance.start()


async def stop_cache_consumer() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
        _instance = None


def get_snapshot(stream_id: uuid.UUID) -> dict[str, Any] | None:
    if _instance is None:
        return None
    return _instance.get_snapshot(stream_id)
