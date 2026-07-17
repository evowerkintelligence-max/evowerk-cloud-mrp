# backend/streaming/test_infra_smoke.py
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="set REDPANDA_BROKERS to run against a live Redpanda (e.g. `docker compose up redpanda`)",
)


def test_produce_and_consume_round_trip():
    from kafka import KafkaConsumer, KafkaProducer

    brokers = os.environ["REDPANDA_BROKERS"]
    topic = "realtime.ticks.smoke-test"

    producer = KafkaProducer(bootstrap_servers=brokers)
    consumer = KafkaConsumer(
        topic, bootstrap_servers=brokers, auto_offset_reset="earliest",
        consumer_timeout_ms=10_000, group_id=f"smoke-{time.time()}",
    )

    producer.send(topic, b"hello-redpanda")
    producer.flush()

    messages = [msg.value for msg in consumer]
    assert b"hello-redpanda" in messages
