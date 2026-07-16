from __future__ import annotations

import asyncio

from streaming.bus import FakeMessageBus
from streaming.schemas import MetricMessage, TickMessage
from tests.streaming_fakes import FakeWindowingLoop


async def test_windowing_loop_computes_throughput_from_buffered_ticks():
    bus = FakeMessageBus()
    loop = FakeWindowingLoop(bus, window_seconds=0.1)
    await loop.start()

    await bus.subscribe("realtime.metrics", "test-consumer")

    tick = TickMessage(
        stream_id="s1", org_id="o1", name="Line 1", column_map={"units_col": "units"},
        row={"units": 10}, status="running", error=None,
        collected_at="2026-07-16T12:00:00+00:00",
    )
    await bus.publish("realtime.ticks", key="s1", value=tick.to_json())
    await bus.publish("realtime.ticks", key="s1", value=tick.to_json())

    raw = await asyncio.wait_for(bus.consume("realtime.metrics", "test-consumer").__anext__(), timeout=1)
    metric = MetricMessage.from_json(raw)

    assert metric.stream_id == "s1"
    assert metric.name == "Line 1"
    assert metric.rows_in_window == 2
    assert metric.metrics["throughput"] == 20.0

    await loop.stop()


async def test_two_streams_are_windowed_independently():
    bus = FakeMessageBus()
    loop = FakeWindowingLoop(bus, window_seconds=0.1)
    await loop.start()
    await bus.subscribe("realtime.metrics", "test-consumer")

    def tick(stream_id: str, units: int) -> TickMessage:
        return TickMessage(
            stream_id=stream_id, org_id="o1", name=stream_id, column_map={"units_col": "units"},
            row={"units": units}, status="running", error=None,
            collected_at="2026-07-16T12:00:00+00:00",
        )

    await bus.publish("realtime.ticks", key="a", value=tick("a", 10).to_json())
    await bus.publish("realtime.ticks", key="b", value=tick("b", 3).to_json())

    seen: dict[str, MetricMessage] = {}
    consumer = bus.consume("realtime.metrics", "test-consumer")
    while len(seen) < 2:
        raw = await asyncio.wait_for(consumer.__anext__(), timeout=1)
        metric = MetricMessage.from_json(raw)
        seen[metric.stream_id] = metric

    assert seen["a"].metrics["throughput"] == 10.0
    assert seen["b"].metrics["throughput"] == 3.0

    await loop.stop()
