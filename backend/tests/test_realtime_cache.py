# backend/tests/test_realtime_cache.py
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="set REDPANDA_BROKERS to run against a live Redpanda",
)


@pytest.mark.asyncio
async def test_cache_consumer_reflects_published_metric_snapshot(monkeypatch):
    from app.core.config import get_settings
    from app.services import realtime_cache
    from streaming.schemas import METRICS_TOPIC, MetricSnapshot

    monkeypatch.setenv("REDPANDA_BROKERS", os.environ["REDPANDA_BROKERS"])
    get_settings.cache_clear()

    stream_id = uuid.uuid4()
    await realtime_cache.start_cache_consumer()
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(bootstrap_servers=os.environ["REDPANDA_BROKERS"])
        await producer.start()
        snap = MetricSnapshot(
            ts="2026-07-17T00:00:05+00:00", stream_id=str(stream_id), name="Line 1",
            status="running", error=None, rows_in_window=2, rows_collected=2,
            duration_s=5, last_tick_s=0.0, metrics={"throughput": 30.0},
        )
        await producer.send_and_wait(METRICS_TOPIC, key=str(stream_id).encode(), value=snap.to_json())
        await producer.stop()

        for _ in range(30):
            got = realtime_cache.get_snapshot(stream_id)
            if got is not None:
                break
            await asyncio.sleep(0.2)

        assert got is not None
        assert got["metrics"]["throughput"] == 30.0
        assert got["rows_collected"] == 2
        # frontend/src/pages/Realtime.tsx:86 reads snap.duration (formatted HH:MM:SS,
        # not duration_s) directly - must survive the swap from StreamRunner.
        assert got["duration"] is not None and ":" in got["duration"]
    finally:
        await realtime_cache.stop_cache_consumer()


@pytest.mark.asyncio
async def test_two_consumer_groups_both_see_every_stream():
    """Proves the per-replica-unique-group-id requirement: two independent
    cache consumer instances (simulating two backend replicas) must each
    receive every message, not a shard of them."""
    from app.services import realtime_cache

    stream_id = uuid.uuid4()

    consumer_a = realtime_cache.CacheConsumer(group_id=f"replica-a-{time.time()}")
    consumer_b = realtime_cache.CacheConsumer(group_id=f"replica-b-{time.time()}")
    await consumer_a.start()
    await consumer_b.start()
    try:
        from aiokafka import AIOKafkaProducer

        from streaming.schemas import METRICS_TOPIC, MetricSnapshot

        producer = AIOKafkaProducer(bootstrap_servers=os.environ["REDPANDA_BROKERS"])
        await producer.start()
        snap = MetricSnapshot(
            ts="2026-07-17T00:00:05+00:00", stream_id=str(stream_id), name="Line 1",
            status="running", error=None, rows_in_window=1, rows_collected=1,
            duration_s=5, last_tick_s=0.0, metrics={"throughput": 10.0},
        )
        await producer.send_and_wait(METRICS_TOPIC, key=str(stream_id).encode(), value=snap.to_json())
        await producer.stop()

        for _ in range(30):
            if consumer_a.get_snapshot(stream_id) and consumer_b.get_snapshot(stream_id):
                break
            await asyncio.sleep(0.2)

        assert consumer_a.get_snapshot(stream_id) is not None
        assert consumer_b.get_snapshot(stream_id) is not None
    finally:
        await consumer_a.stop()
        await consumer_b.stop()
