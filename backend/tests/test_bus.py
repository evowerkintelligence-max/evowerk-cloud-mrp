from __future__ import annotations

import asyncio

from streaming.bus import FakeMessageBus


async def test_subscribed_consumer_receives_published_message():
    bus = FakeMessageBus()
    await bus.subscribe("realtime.ticks", "group-a")

    await bus.publish("realtime.ticks", key="s1", value=b"hello")

    msg = await bus.consume("realtime.ticks", "group-a").__anext__()
    assert msg == b"hello"


async def test_two_distinct_groups_both_receive_every_message():
    """Mirrors Kafka's per-consumer-group broadcast semantics - this is what
    backs the "unique group per replica" requirement in the design doc."""
    bus = FakeMessageBus()
    await bus.subscribe("realtime.metrics", "replica-a")
    await bus.subscribe("realtime.metrics", "replica-b")

    await bus.publish("realtime.metrics", key="s1", value=b"snapshot-1")

    a = await bus.consume("realtime.metrics", "replica-a").__anext__()
    b = await bus.consume("realtime.metrics", "replica-b").__anext__()
    assert a == b"snapshot-1"
    assert b == b"snapshot-1"


async def test_message_published_before_subscribe_is_not_delivered():
    """Documents the one real gap vs. a durable Redpanda log: the fake has no
    replay. Anything relying on replay-from-earliest is verified against real
    Redpanda in Task 9's manual check, not this fake."""
    bus = FakeMessageBus()
    await bus.publish("realtime.ticks", key="s1", value=b"missed")
    await bus.subscribe("realtime.ticks", "late-group")

    with pytest_raises_timeout():
        await asyncio.wait_for(bus.consume("realtime.ticks", "late-group").__anext__(), timeout=0.05)


def pytest_raises_timeout():
    import pytest
    return pytest.raises(asyncio.TimeoutError)
