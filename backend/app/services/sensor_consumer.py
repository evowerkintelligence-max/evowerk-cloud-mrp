"""Asyncio background consumer of the realtime.ticks Redpanda topic - drains
ticks for streams that have a sensor_map configured, extracts named sensor
channels, persists them as SensorReading rows, and scores each new reading
via app.services.anomaly. Mirrors app.services.realtime_cache.CacheConsumer's
structure (asyncio background task) but writes to Postgres instead of an
in-memory snapshot cache. Unlike CacheConsumer, all replicas share a single
consumer group here so Kafka partitions the tick stream across them instead
of every replica processing every tick - this consumer does durable writes,
where duplicate processing would mean duplicate rows and racing upserts.

ponytail: no in-memory RealtimeStream cache - looks the stream up per tick.
At typical 5s+ poll intervals this is a bounded, cheap query; add caching
only if this consumer is shown to be DB-bound."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.services.sensor_ingest import ingest_rows
from streaming.schemas import TICKS_TOPIC, Tick

logger = logging.getLogger(__name__)


class SensorConsumer:
    def __init__(self, group_id: str | None = None, brokers: str | None = None):
        self.group_id = group_id or "sensor-consumer"
        self.brokers = brokers or get_settings().redpanda_brokers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            TICKS_TOPIC, bootstrap_servers=self.brokers,
            group_id=self.group_id, auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("sensor_consumer (group=%s) died: %r", self.group_id, exc)

    async def _run(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            try:
                tick = Tick.from_json(msg.value)
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
            try:
                await self._handle_tick(tick)
            except Exception:
                logger.exception("sensor_consumer failed to process tick for stream %s", tick.stream_id)

    async def _handle_tick(self, tick: Tick) -> None:
        if tick.status != "running" or tick.error:
            return

        async with db_session.async_session_factory() as db:
            stream = (
                await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(tick.stream_id)))
            ).scalar_one_or_none()
            if stream is None:
                return
            await ingest_rows(db, stream, [tick.row])
            await db.commit()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer is not None:
            await self._consumer.stop()


_instance: SensorConsumer | None = None


async def start_sensor_consumer(brokers: str | None = None) -> None:
    global _instance
    consumer = SensorConsumer(brokers=brokers)
    try:
        await consumer.start()
    except Exception:
        # No Kafka/Redpanda reachable (e.g. AWS deploy, which doesn't
        # provision one) - degrade to "no sensor ingestion" instead of
        # failing app startup entirely, same as realtime_cache.
        logger.error(
            "sensor_consumer failed to start (brokers=%s) - sensor ingestion disabled",
            consumer.brokers, exc_info=True,
        )
        return
    _instance = consumer


async def stop_sensor_consumer() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
        _instance = None
