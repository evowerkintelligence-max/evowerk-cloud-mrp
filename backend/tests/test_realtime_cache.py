from __future__ import annotations

import asyncio

from app.services.realtime_cache import RealtimeCache
from streaming.bus import FakeMessageBus
from streaming.schemas import MetricMessage


def _metric(stream_id: str, throughput: float, duration_s: int = 300) -> MetricMessage:
    return MetricMessage(
        stream_id=stream_id, name="Line 3", status="running", error=None,
        rows_in_window=5, rows_collected=50, duration_s=duration_s, last_tick_s=1.0,
        metrics={"throughput": throughput}, ts="2026-07-16T12:00:00+00:00",
    )


async def _wait_for(cache: RealtimeCache, stream_id: str) -> None:
    while cache.get_snapshot(stream_id) is None:
        await asyncio.sleep(0.01)


async def test_cache_reflects_latest_metric_message_for_a_stream():
    bus = FakeMessageBus()
    cache = RealtimeCache(bus, group_id="replica-1")
    await cache.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 10.0).to_json())
    await asyncio.wait_for(_wait_for(cache, "s1"), timeout=1)

    snap = cache.get_snapshot("s1")
    assert snap["metrics"]["throughput"] == 10.0
    assert snap["duration"] == "00:05:00"

    await cache.stop()


async def test_two_cache_instances_with_distinct_groups_both_see_the_update():
    """The correctness property the whole design hinges on: two backend
    replicas, each running their own RealtimeCache with a distinct group_id
    against the same bus, must both learn about every stream's update."""
    bus = FakeMessageBus()
    cache_a = RealtimeCache(bus, group_id="replica-a")
    cache_b = RealtimeCache(bus, group_id="replica-b")
    await cache_a.start()
    await cache_b.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 42.0).to_json())
    await asyncio.wait_for(_wait_for(cache_a, "s1"), timeout=1)
    await asyncio.wait_for(_wait_for(cache_b, "s1"), timeout=1)

    assert cache_a.get_snapshot("s1")["metrics"]["throughput"] == 42.0
    assert cache_b.get_snapshot("s1")["metrics"]["throughput"] == 42.0

    await cache_a.stop()
    await cache_b.stop()


async def test_on_update_callback_fires_with_stream_id_and_snapshot():
    seen = []
    bus = FakeMessageBus()
    cache = RealtimeCache(bus, group_id="replica-1", on_update=lambda sid, snap: seen.append((sid, snap)))
    await cache.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 7.0).to_json())
    await asyncio.wait_for(_wait_for(cache, "s1"), timeout=1)

    assert len(seen) == 1
    assert seen[0][0] == "s1"
    assert seen[0][1]["metrics"]["throughput"] == 7.0

    await cache.stop()
