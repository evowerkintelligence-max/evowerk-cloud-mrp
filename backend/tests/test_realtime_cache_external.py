"""Hermetic tests for realtime_cache.py's recent_rows()/set_external_metric()
extension points - exercised by calling CacheConsumer's message handlers
directly with encoded Tick/MetricSnapshot bytes, no live Redpanda broker
needed (unlike test_realtime_cache.py's wire-level tests)."""
from __future__ import annotations

import uuid

from app.services import realtime_cache
from streaming.schemas import MetricSnapshot, Tick


def _consumer() -> realtime_cache.CacheConsumer:
    return realtime_cache.CacheConsumer()


def test_recent_rows_returns_ticks_oldest_first_bounded_to_n():
    stream_id = uuid.uuid4()
    consumer = _consumer()
    for i in range(5):
        tick = Tick(
            stream_id=str(stream_id), org_id="", column_map={}, row={"i": i},
            status="running", error=None, collected_at="2026-07-17T00:00:00+00:00",
        )
        consumer._handle_tick(tick.to_json())

    assert consumer.recent_rows(stream_id) == [{"i": i} for i in range(5)]
    assert consumer.recent_rows(stream_id, n=2) == [{"i": 3}, {"i": 4}]
    assert consumer.recent_rows(uuid.uuid4()) == []  # unknown stream - empty, not an error


def test_set_external_metric_merges_into_get_snapshot():
    stream_id = uuid.uuid4()
    consumer = _consumer()
    snap = MetricSnapshot(
        ts="2026-07-17T00:00:05+00:00", stream_id=str(stream_id), name="Line 1",
        status="running", error=None, rows_in_window=2, rows_collected=2,
        duration_s=5, last_tick_s=0.0, metrics={"throughput": 30.0},
    )
    consumer._handle_metric_snapshot(snap.to_json())

    consumer.set_external_metric(stream_id, "automl_risk_score", 0.87)

    got = consumer.get_snapshot(stream_id)
    assert got["metrics"] == {"throughput": 30.0, "automl_risk_score": 0.87}
    assert got["rows_collected"] == 2  # rest of the Flink-computed snapshot is untouched


def test_set_external_metric_before_any_flink_snapshot_still_visible():
    """A live AutoML score can arrive before this stream's first Flink-
    computed MetricSnapshot - eval_stream_metric must still see it rather
    than get_snapshot() returning None and hiding it."""
    stream_id = uuid.uuid4()
    consumer = _consumer()
    consumer.set_external_metric(stream_id, "automl_risk_score", 0.42)

    got = consumer.get_snapshot(stream_id)
    assert got == {"metrics": {"automl_risk_score": 0.42}}


def test_get_snapshot_none_when_stream_entirely_unknown():
    consumer = _consumer()
    assert consumer.get_snapshot(uuid.uuid4()) is None
