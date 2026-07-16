"""In-memory {stream_id: latest snapshot} cache fed by the realtime.metrics
topic - replaces StreamRunner's per-process snapshot state. Any backend
replica running this consumer sees every stream's latest metrics, not just
the one that happens to run a given stream's producer, PROVIDED each
replica's RealtimeCache uses a distinct group_id (enforced by the caller -
see app/main.py's lifespan wiring in Task 8). See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from streaming.bus import MessageBus
from streaming.schemas import MetricMessage

METRICS_TOPIC = "realtime.metrics"


class RealtimeCache:
    def __init__(
        self,
        bus: MessageBus,
        group_id: str,
        on_update: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._bus = bus
        self._group_id = group_id
        self._on_update = on_update
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None

    def get_snapshot(self, stream_id: str) -> dict[str, Any] | None:
        return self._snapshots.get(stream_id)

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        return dict(self._snapshots)

    async def start(self) -> None:
        await self._bus.subscribe(METRICS_TOPIC, self._group_id)
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _consume_loop(self) -> None:
        async for raw in self._bus.consume(METRICS_TOPIC, self._group_id):
            msg = MetricMessage.from_json(raw)
            h, rem = divmod(msg.duration_s, 3600)
            m, s = divmod(rem, 60)
            snapshot = {
                "ts": msg.ts,
                "stream_id": msg.stream_id,
                "name": msg.name,
                "status": msg.status,
                "error": msg.error,
                "rows_in_window": msg.rows_in_window,
                "rows_collected": msg.rows_collected,
                "duration": f"{h:02d}:{m:02d}:{s:02d}",
                "duration_s": msg.duration_s,
                "last_tick_s": msg.last_tick_s,
                "metrics": msg.metrics,
            }
            self._snapshots[msg.stream_id] = snapshot
            if self._on_update is not None:
                self._on_update(msg.stream_id, snapshot)
