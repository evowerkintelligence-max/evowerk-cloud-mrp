# backend/streaming/schemas.py
"""Message shapes shared by the tick producer (app.services.realtime),
the Flink windowed-aggregation job (streaming.realtime_metrics_job), and the
cache consumer (app.services.realtime_cache) - the one place all three agree
on wire format, so the JSON shape can't drift between them."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

TICKS_TOPIC = "realtime.ticks"
METRICS_TOPIC = "realtime.metrics"


@dataclass(frozen=True)
class Tick:
    stream_id: str
    org_id: str
    column_map: dict[str, str]
    row: dict[str, Any]
    status: str
    error: str | None
    collected_at: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> "Tick":
        return cls(**json.loads(data))


@dataclass(frozen=True)
class MetricSnapshot:
    ts: str
    stream_id: str
    name: str
    status: str
    error: str | None
    rows_in_window: int
    rows_collected: int
    duration_s: int
    last_tick_s: float | None
    metrics: dict[str, Any]

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> "MetricSnapshot":
        return cls(**json.loads(data))
