# backend/streaming/test_schemas.py
from __future__ import annotations

from streaming.schemas import METRICS_TOPIC, TICKS_TOPIC, MetricSnapshot, Tick


def test_tick_round_trips_through_json():
    tick = Tick(
        stream_id="11111111-1111-1111-1111-111111111111",
        org_id="22222222-2222-2222-2222-222222222222",
        column_map={"units_col": "units"},
        row={"units": 10, "scrap": 1},
        status="running",
        error=None,
        collected_at="2026-07-17T00:00:00+00:00",
    )
    restored = Tick.from_json(tick.to_json())
    assert restored == tick


def test_metric_snapshot_round_trips_through_json():
    snap = MetricSnapshot(
        ts="2026-07-17T00:00:05+00:00",
        stream_id="11111111-1111-1111-1111-111111111111",
        name="Line 1",
        status="running",
        error=None,
        rows_in_window=3,
        rows_collected=42,
        duration_s=300,
        last_tick_s=1.2,
        metrics={"throughput": 10.0, "scrap_rate_pct": 10.0},
    )
    restored = MetricSnapshot.from_json(snap.to_json())
    assert restored == snap


def test_topic_names_are_stable_constants():
    assert TICKS_TOPIC == "realtime.ticks"
    assert METRICS_TOPIC == "realtime.metrics"
