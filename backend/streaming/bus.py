"""Message bus abstraction for the realtime pipeline. FakeMessageBus is an
in-memory stand-in used by the test suite (Tasks 5-8); RedpandaMessageBus
(Task 4, same file) is the real aiokafka-backed implementation used in
production. Both satisfy the same MessageBus protocol so app code never
branches on which one it has. See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Protocol

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer


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


class RedpandaMessageBus:
    """Real implementation, talking to Redpanda over its Kafka-compatible
    wire protocol via aiokafka. See
    docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""

    def __init__(self, brokers: str) -> None:
        self._brokers = brokers
        self._producer: AIOKafkaProducer | None = None
        self._consumers: dict[tuple[str, str], AIOKafkaConsumer] = {}

    async def _producer_ref(self) -> AIOKafkaProducer:
        if self._producer is None:
            self._producer = AIOKafkaProducer(bootstrap_servers=self._brokers)
            await self._producer.start()
        return self._producer

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        producer = await self._producer_ref()
        await producer.send_and_wait(topic, value=value, key=key.encode("utf-8"))

    async def subscribe(self, topic: str, group_id: str) -> None:
        if (topic, group_id) in self._consumers:
            return
        consumer = AIOKafkaConsumer(topic, bootstrap_servers=self._brokers, group_id=group_id)
        await consumer.start()
        self._consumers[(topic, group_id)] = consumer

    async def consume(self, topic: str, group_id: str) -> AsyncIterator[bytes]:
        await self.subscribe(topic, group_id)
        consumer = self._consumers[(topic, group_id)]
        async for record in consumer:
            yield record.value

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
        for consumer in self._consumers.values():
            await consumer.stop()
