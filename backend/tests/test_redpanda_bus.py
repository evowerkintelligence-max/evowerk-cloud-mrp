from __future__ import annotations

from unittest.mock import AsyncMock, patch

from streaming.bus import RedpandaMessageBus


async def test_publish_starts_producer_once_and_sends_with_key():
    with patch("streaming.bus.AIOKafkaProducer") as MockProducer:
        producer_instance = AsyncMock()
        MockProducer.return_value = producer_instance

        bus = RedpandaMessageBus(brokers="localhost:9092")
        await bus.publish("realtime.ticks", key="stream-1", value=b"payload")
        await bus.publish("realtime.ticks", key="stream-1", value=b"payload-2")

        MockProducer.assert_called_once_with(bootstrap_servers="localhost:9092")
        producer_instance.start.assert_awaited_once()
        assert producer_instance.send_and_wait.await_count == 2
        producer_instance.send_and_wait.assert_any_call("realtime.ticks", value=b"payload", key=b"stream-1")


async def test_subscribe_is_idempotent_per_topic_and_group():
    with patch("streaming.bus.AIOKafkaConsumer") as MockConsumer:
        consumer_instance = AsyncMock()
        MockConsumer.return_value = consumer_instance

        bus = RedpandaMessageBus(brokers="localhost:9092")
        await bus.subscribe("realtime.metrics", "group-a")
        await bus.subscribe("realtime.metrics", "group-a")

        MockConsumer.assert_called_once_with(
            "realtime.metrics", bootstrap_servers="localhost:9092", group_id="group-a"
        )
        consumer_instance.start.assert_awaited_once()


async def test_close_stops_producer_and_all_consumers():
    with patch("streaming.bus.AIOKafkaProducer") as MockProducer, \
         patch("streaming.bus.AIOKafkaConsumer") as MockConsumer:
        producer_instance = AsyncMock()
        MockProducer.return_value = producer_instance
        consumer_instance = AsyncMock()
        MockConsumer.return_value = consumer_instance

        bus = RedpandaMessageBus(brokers="localhost:9092")
        await bus.publish("realtime.ticks", key="s1", value=b"x")
        await bus.subscribe("realtime.metrics", "group-a")

        await bus.close()

        producer_instance.stop.assert_awaited_once()
        consumer_instance.stop.assert_awaited_once()
