# backend/streaming/schemas.py
"""Message shapes shared by the tick producer (app.services.realtime),
the Flink windowed-aggregation job (streaming.realtime_metrics_job), and the
cache consumer (app.services.realtime_cache) - the one place all three agree
on wire format, so the JSON shape can't drift between them."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

TICKS_TOPIC = "realtime.ticks"
METRICS_TOPIC = "realtime.metrics"


def _json_default(obj: Any) -> Any:
    """Connector rows carry whatever type the source driver hands back -
    openpyxl gives native datetime/date for Excel date cells, pyodbc/
    cx_Oracle give datetime/date/Decimal for DATE and NUMERIC columns - none
    of which json.dumps handles natively. Without this, a single date column
    anywhere in the row makes every tick fail to serialize, which previously
    surfaced as an opaque "not_running_in_this_process"/stuck-at-0-rows
    status rather than a real error."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


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
        return json.dumps(asdict(self), default=_json_default).encode("utf-8")

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
        return json.dumps(asdict(self), default=_json_default).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> "MetricSnapshot":
        return cls(**json.loads(data))
