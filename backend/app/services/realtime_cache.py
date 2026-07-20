# backend/app/services/realtime_cache.py
"""Asyncio background consumer of the realtime.metrics and realtime.ticks
Redpanda topics - the replacement for reading a StreamRunner's in-memory
state directly. Every backend replica runs its own CacheConsumer with a
unique consumer group, so every replica sees every stream's updates (a
shared group would split partitions across replicas and reintroduce the
exact "not running in this process" bug this whole pipeline exists to fix).
Also persists each snapshot to the same JSONL history file
app.services.realtime's StreamRunner used to write directly, so
stream_history() keeps working unchanged.

Also the home for two small extension points app.services.automl_realtime
uses to score/drift-check a stream and publish its own metric back:
recent_rows() (raw per-row tick data - the Flink-computed `metrics` a
MetricSnapshot carries are windowed aggregates, not the feature columns a
trained pipeline needs) and set_external_metric() (merged into
get_snapshot()'s `metrics` dict so the existing stream_metric alert path,
app.services.alerts.eval_stream_metric, can alert on it without knowing
anything about AutoML). Kept generic/minimal rather than AutoML-named, same
posture as the rest of this module."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from collections import deque
from typing import Any

from aiokafka import AIOKafkaConsumer

from app.core.config import get_settings
from app.services.realtime import history_path
from streaming.schemas import METRICS_TOPIC, TICKS_TOPIC, MetricSnapshot, Tick

logger = logging.getLogger(__name__)

# Raw rows kept per stream for recent_rows() - large enough to cover
# automl_realtime's biggest single ask (a drift-triggered retrain pulls up
# to 10k rows), small enough to stay cheap per replica.
_RAW_ROW_BUFFER = 10_000


class CacheConsumer:
    def __init__(self, group_id: str | None = None, brokers: str | None = None):
        self.group_id = group_id or f"realtime-cache-{socket.gethostname()}-{os.getpid()}"
        self.brokers = brokers or get_settings().redpanda_brokers
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._raw_rows: dict[str, deque[dict[str, Any]]] = {}
        self._external_metrics: dict[str, dict[str, Any]] = {}
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            METRICS_TOPIC, TICKS_TOPIC, bootstrap_servers=self.brokers,
            group_id=self.group_id, auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        # _run() only ends via stop()'s cancel (swallowed there) or an
        # unhandled exception (e.g. the Kafka connection drops) - the latter
        # leaves get_snapshot() silently returning stale data forever with
        # no reconnect. No retry logic here (accepted v1 gap), but at least
        # log it so an operator can see this replica's cache died.
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("realtime_cache consumer (group=%s) died: %r", self.group_id, exc)

    async def _run(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            if msg.topic == TICKS_TOPIC:
                self._handle_tick(msg.value)
            else:
                self._handle_metric_snapshot(msg.value)

    def _handle_tick(self, raw: bytes) -> None:
        try:
            tick = Tick.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError):
            return
        buf = self._raw_rows.setdefault(tick.stream_id, deque(maxlen=_RAW_ROW_BUFFER))
        buf.append(tick.row)

    def _handle_metric_snapshot(self, raw: bytes) -> None:
        try:
            snap = MetricSnapshot.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError):
            return
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
        sid = str(stream_id)
        snap = self._snapshots.get(sid)
        external = self._external_metrics.get(sid)
        if not external:
            return snap
        # An external metric (e.g. a live AutoML risk score) can exist before
        # this stream's first Flink-computed snapshot arrives - synthesize a
        # minimal one rather than hiding the metric entirely. eval_stream_metric
        # only ever reads the "metrics" key, so this is a safe partial shape.
        if snap is None:
            return {"metrics": dict(external)}
        return {**snap, "metrics": {**snap["metrics"], **external}}

    def recent_rows(self, stream_id: uuid.UUID, n: int = 500) -> list[dict[str, Any]]:
        """Last `n` raw tick rows for a stream, oldest first - the actual
        ingested feature columns, not the Flink job's windowed aggregates."""
        rows = self._raw_rows.get(str(stream_id))
        if not rows:
            return []
        rows = list(rows)
        return rows[-max(1, int(n)):]

    def set_external_metric(self, stream_id: uuid.UUID, name: str, value: Any) -> None:
        self._external_metrics.setdefault(str(stream_id), {})[name] = value

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
    consumer = CacheConsumer(brokers=brokers)
    try:
        await consumer.start()
    except Exception:
        # No Kafka/Redpanda reachable (e.g. AWS deploy, which doesn't
        # provision one) - degrade to "no live realtime metrics" instead of
        # failing app startup entirely. get_snapshot() already returns None
        # when _instance is None.
        logger.error(
            "realtime_cache consumer failed to start (brokers=%s) - realtime metrics disabled",
            consumer.brokers, exc_info=True,
        )
        return
    _instance = consumer


async def stop_cache_consumer() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
        _instance = None


def get_snapshot(stream_id: uuid.UUID) -> dict[str, Any] | None:
    if _instance is None:
        return None
    return _instance.get_snapshot(stream_id)


def recent_rows(stream_id: uuid.UUID, n: int = 500) -> list[dict[str, Any]]:
    if _instance is None:
        return []
    return _instance.recent_rows(stream_id, n)


def set_external_metric(stream_id: uuid.UUID, name: str, value: Any) -> None:
    if _instance is not None:
        _instance.set_external_metric(stream_id, name, value)
