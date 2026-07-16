"""Test-only stand-in for the real PyFlink job (Task 9) - reads raw ticks off
the FakeMessageBus, windows them in-process on a short fixed interval, and
republishes MetricMessages, so the rest of the pipeline (producer -> bus ->
cache -> routes/alerts) can be exercised end-to-end in the pytest suite
without a real Redpanda+Flink cluster. Uses the exact same
streaming.metrics_calc formulas the real Flink job uses, so results match
between test and production."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from streaming.bus import FakeMessageBus
from streaming.metrics_calc import compute_throughput_rate, compute_window_metrics, safe_float
from streaming.schemas import MetricMessage, TickMessage

TICKS_TOPIC = "realtime.ticks"
METRICS_TOPIC = "realtime.metrics"


class FakeWindowingLoop:
    def __init__(self, bus: FakeMessageBus, window_seconds: float = 0.2):
        self._bus = bus
        self._window_seconds = window_seconds
        self._group_id = f"fake-flink-{uuid.uuid4()}"
        self._started_at: dict[str, float] = {}
        self._cumulative_units: dict[str, float] = defaultdict(float)
        self._rows_collected: dict[str, int] = defaultdict(int)
        self._window: dict[str, list[TickMessage]] = defaultdict(list)
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self._bus.subscribe(TICKS_TOPIC, self._group_id)
        # Two tasks instead of one wait_for-cancel loop: cancelling a shared
        # async generator's __anext__ (the plan's original single-loop shape)
        # kills the generator on Python 3.14, so the consumer must never be
        # cancelled mid-read. The consumer buffers ticks; the flusher windows
        # them on a fixed cadence.
        self._tasks = [
            asyncio.create_task(self._consume()),
            asyncio.create_task(self._flush_loop()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def _consume(self) -> None:
        async for raw in self._bus.consume(TICKS_TOPIC, self._group_id):
            msg = TickMessage.from_json(raw)
            self._window[msg.stream_id].append(msg)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._window_seconds)
            window, self._window = self._window, defaultdict(list)
            await self._flush(window)

    async def _flush(self, window: dict[str, list[TickMessage]]) -> None:
        for stream_id, ticks in window.items():
            if not ticks:
                continue
            if stream_id not in self._started_at:
                self._started_at[stream_id] = time.monotonic()

            rows = [t.row for t in ticks]
            column_map = ticks[-1].column_map
            metrics = compute_window_metrics(rows, column_map)

            units_col = column_map.get("units_col")
            if units_col:
                self._cumulative_units[stream_id] += sum(safe_float(r.get(units_col, 0)) for r in rows)
            duration_s = int(time.monotonic() - self._started_at[stream_id])
            rate = compute_throughput_rate(self._cumulative_units[stream_id], duration_s)
            if rate is not None:
                metrics["throughput_rate"] = rate
            self._rows_collected[stream_id] += len(rows)

            out = MetricMessage(
                stream_id=stream_id,
                name=ticks[-1].name,
                status=ticks[-1].status,
                error=ticks[-1].error,
                rows_in_window=len(rows),
                rows_collected=self._rows_collected[stream_id],
                duration_s=duration_s,
                last_tick_s=0.0,
                metrics=metrics,
                ts=datetime.now(timezone.utc).isoformat(),
            )
            await self._bus.publish(METRICS_TOPIC, key=stream_id, value=out.to_json())
