# backend/tests/test_realtime_producer.py
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="set REDPANDA_BROKERS to run against a live Redpanda",
)


class _FakeConnector:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self):
        return self._rows


def test_tick_producer_publishes_polled_rows_to_redpanda(monkeypatch):
    from kafka import KafkaConsumer

    from app.services import realtime as realtime_service
    from streaming.schemas import TICKS_TOPIC, Tick

    monkeypatch.setattr(
        realtime_service, "get_connector",
        lambda integration_type, config: _FakeConnector([{"units": 10, "scrap": 1}]),
    )

    consumer = KafkaConsumer(
        TICKS_TOPIC, bootstrap_servers=os.environ["REDPANDA_BROKERS"],
        auto_offset_reset="latest", consumer_timeout_ms=8_000,
        group_id=f"test-tick-producer-{time.time()}",
    )
    # Prime the consumer group's partition assignment before publishing.
    consumer.poll(timeout_ms=1000)

    producer = realtime_service.TickProducer(
        stream_id="33333333-3333-3333-3333-333333333333",
        name="Test Stream",
        integration_type="erp_rest",
        integration_config={},
        column_map={"units_col": "units", "scrap_col": "scrap"},
        interval_seconds=1,
        accumulate=True,
    )
    try:
        received = None
        for msg in consumer:
            tick = Tick.from_json(msg.value)
            if tick.stream_id == "33333333-3333-3333-3333-333333333333":
                received = tick
                break
        assert received is not None
        assert received.row == {"units": 10, "scrap": 1}
        assert received.column_map == {"units_col": "units", "scrap_col": "scrap"}
        assert received.status == "running"
    finally:
        producer.stop()
        consumer.close()
