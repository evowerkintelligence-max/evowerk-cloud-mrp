"""Message bus abstraction for the realtime pipeline. FakeMessageBus is an
in-memory stand-in used by the test suite (Tasks 5-8); RedpandaMessageBus
(Task 4, same file) is the real aiokafka-backed implementation used in
production. Both satisfy the same MessageBus protocol so app code never
branches on which one it has. See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Protocol


class MessageBus(Protocol):
    async def publish(self, topic: str, key: str, value: bytes) -> None: ...
    async def subscribe(self, topic: str, group_id: str) -> None: ...
    def consume(self, topic: str, group_id: str) -> AsyncIterator[bytes]: ...


class FakeMessageBus:
    """In-memory pub/sub. Every distinct (topic, group_id) gets its own
    queue and sees every message published to that topic after subscribing -
    mirrors Kafka's per-consumer-group broadcast semantics. Callers that need
    a guarantee of "no message lost between subscribing and the first read"
    must call `await bus.subscribe(...)` explicitly before iterating
    `consume()` - `consume()` also calls it defensively, but only once the
    async generator is actually iterated, which can be later than the caller
    expects."""

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], "asyncio.Queue[bytes]"] = {}

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        for (t, _group), q in list(self._queues.items()):
            if t == topic:
                await q.put(value)

    async def subscribe(self, topic: str, group_id: str) -> None:
        self._queues.setdefault((topic, group_id), asyncio.Queue())

    async def consume(self, topic: str, group_id: str) -> AsyncIterator[bytes]:
        await self.subscribe(topic, group_id)
        q = self._queues[(topic, group_id)]
        while True:
            yield await q.get()
