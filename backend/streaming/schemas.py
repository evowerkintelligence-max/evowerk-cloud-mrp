"""Shared message shapes for the realtime Redpanda+Flink pipeline. Used by
the producer (app/services/realtime_producer.py), the real Flink job
(streaming/realtime_metrics_job.py), the test fake windower
(tests/streaming_fakes.py), and the cache consumer
(app/services/realtime_cache.py) so the JSON shape can't drift between them.
See docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TickMessage:
    stream_id: str
    org_id: str
    name: str
    column_map: dict[str, str]
    row: dict[str, Any]
    status: str
    error: str | None
    collected_at: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @staticmethod
    def from_json(data: bytes) -> "TickMessage":
        return TickMessage(**json.loads(data.decode("utf-8")))


@dataclass
class MetricMessage:
    stream_id: str
    name: str
    status: str
    error: str | None
    rows_in_window: int
    rows_collected: int
    duration_s: int
    last_tick_s: float | None
    metrics: dict[str, Any]
    ts: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @staticmethod
    def from_json(data: bytes) -> "MetricMessage":
        return MetricMessage(**json.loads(data.decode("utf-8")))
