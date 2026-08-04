# backend/tests/test_realtime_local_fallback.py
"""Covers TickProducer's local-metrics fallback (app/services/realtime.py)
for a deployment that never provisioned Redpanda/Kafka - the scenario
test_realtime_producer.py can't cover, since that whole file is skipped
unless REDPANDA_BROKERS is set. This monkeypatches KafkaProducer itself to
fail construction, so it runs unconditionally and proves the fallback
without needing any live infra at all."""
from __future__ import annotations

import threading
import time

import pytest

from app.services import realtime as realtime_service


class _FakeConnector:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self):
        return self._rows


class _AlwaysFailsProducer:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("no brokers available")


def test_tick_producer_computes_metrics_locally_when_redpanda_unreachable(monkeypatch):
    monkeypatch.setattr(
        realtime_service, "get_connector",
        lambda integration_type, config: _FakeConnector([{"units": 10, "scrap": 1}]),
    )
    monkeypatch.setattr(realtime_service, "KafkaProducer", _AlwaysFailsProducer)

    producer = realtime_service.TickProducer(
        stream_id="44444444-4444-4444-4444-444444444444",
        name="Local Fallback Stream",
        integration_type="erp_rest",
        integration_config={},
        column_map={"units_col": "units", "scrap_col": "scrap", "total_units_col": "units"},
        interval_seconds=1,
        accumulate=True,
    )
    try:
        snap = None
        for _ in range(50):
            snap = producer.local_snapshot()
            if snap is not None:
                break
            time.sleep(0.2)

        assert snap is not None, "local_snapshot() never populated - fallback did not engage"
        assert snap["status"] == "running"
        assert snap["error"] is None
        assert snap["rows_collected"] >= 1
        assert snap["metrics"]["throughput"] == pytest.approx(10.0)
        assert snap["metrics"]["scrap_rate_pct"] == pytest.approx(10.0)
        assert snap["last_tick_s"] is not None
    finally:
        producer.stop()

    # Once stopped, the last computed snapshot is still readable (matches the
    # Kafka-backed path: a stopped stream's last-known data isn't wiped, only
    # _snapshot_dict's desired_status guard stops it from being surfaced).
    assert producer.local_snapshot() is not None


def test_local_snapshot_none_when_kafka_connects_fine():
    """local_snapshot() must defer to the Kafka/Flink pipeline whenever
    Redpanda IS reachable, not shadow it with locally-computed data."""

    class _FakeWorkingProducer:
        def __init__(self, *a, **k):
            pass

        def send(self, *a, **k):
            pass

        def flush(self):
            pass

        def close(self, timeout=None):
            pass

    producer = realtime_service.TickProducer.__new__(realtime_service.TickProducer)
    producer.stream_id = "66666666-6666-6666-6666-666666666666"
    producer.name = "Kafka up"
    producer.column_map = {}
    producer.status = "running"
    producer.error = None
    producer._local_lock = threading.Lock()
    producer._local_snapshot = None
    producer._rows_collected = 0
    producer._started_at = None
    producer._last_tick_at = None
    producer._producer = _FakeWorkingProducer()

    assert producer.local_snapshot() is None


def test_local_snapshot_none_before_first_poll(monkeypatch):
    """Proves local_snapshot() stays None until _record_local has actually run
    at least once - a stream that hasn't polled yet must not fabricate data,
    it should fall through to the existing "not seen yet" status/error path."""
    ready = threading.Event()

    class _BlockingConnector:
        def fetch_all(self):
            ready.wait(timeout=5)
            return [{"units": 1}]

    monkeypatch.setattr(realtime_service, "get_connector", lambda t, c: _BlockingConnector())
    monkeypatch.setattr(realtime_service, "KafkaProducer", _AlwaysFailsProducer)

    producer = realtime_service.TickProducer(
        stream_id="55555555-5555-5555-5555-555555555555",
        name="Not yet connected",
        integration_type="erp_rest",
        integration_config={},
        column_map={},
        interval_seconds=1,
        accumulate=True,
    )
    try:
        # The background thread is blocked inside fetch_all() (via `ready`),
        # so _record_local hasn't run yet - deterministic, no sleep-and-hope.
        assert producer.local_snapshot() is None
    finally:
        ready.set()
        producer.stop()
