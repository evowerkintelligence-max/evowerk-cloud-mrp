# backend/streaming/realtime_metrics_job.py
"""PyFlink job: consumes realtime.ticks, keyed by stream_id, 5s tumbling
window, emits one MetricSnapshot per (stream_id, window) to realtime.metrics.
rows_collected and duration_s/started_at are carried as Flink keyed state
across windows (not window-local) - same semantics TickProducer's predecessor
(StreamRunner) tracked as plain instance attributes. status/error pass
through from the latest tick seen in the window untouched - that's the
producer's connector-health signal, not something this job computes.

Run: flink run --python realtime_metrics_job.py --jarfile /opt/flink/lib/flink-sql-connector-kafka.jar
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from pyflink.common import Time, Types, WatermarkStrategy
from pyflink.datastream import ProcessWindowFunction, RuntimeContext, StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import (
    KafkaOffsetsInitializer,
    KafkaRecordSerializationSchema,
    KafkaSink,
    KafkaSource,
)
from pyflink.datastream.state import ValueStateDescriptor
from pyflink.datastream.window import TumblingProcessingTimeWindows

from streaming.metrics import compute_window_metrics
from streaming.schemas import METRICS_TOPIC, TICKS_TOPIC, MetricSnapshot, Tick

WINDOW_SECONDS = 5


class MetricsWindowFunction(ProcessWindowFunction):
    def open(self, runtime_context: RuntimeContext):
        self.rows_collected_state = runtime_context.get_state(
            ValueStateDescriptor("rows_collected", Types.LONG())
        )
        self.started_at_state = runtime_context.get_state(
            ValueStateDescriptor("started_at", Types.STRING())
        )

    def process(self, key: str, context: "ProcessWindowFunction.Context", elements):
        ticks = [Tick.from_json(e) for e in elements]
        rows = [t.row for t in ticks]
        column_map = ticks[-1].column_map if ticks else {}
        status = ticks[-1].status if ticks else "running"
        error = ticks[-1].error if ticks else None
        name = key  # RealtimeStream.name isn't in the tick payload for v1 - the cache consumer already has it from RealtimeStream and can overlay it; see Task 5.

        prior_collected = self.rows_collected_state.value() or 0
        total_collected = prior_collected + len(rows)
        self.rows_collected_state.update(total_collected)

        started_at = self.started_at_state.value()
        now = datetime.now(timezone.utc)
        if started_at is None:
            started_at = now.isoformat()
            self.started_at_state.update(started_at)
        duration_s = int((now - datetime.fromisoformat(started_at)).total_seconds())

        metrics = compute_window_metrics(rows, column_map)

        snap = MetricSnapshot(
            ts=now.isoformat(),
            stream_id=key,
            name=name,
            status=status,
            error=error,
            rows_in_window=len(rows),
            rows_collected=total_collected,
            duration_s=duration_s,
            last_tick_s=0.0 if ticks else None,
            metrics=metrics,
        )
        yield snap.to_json()


def build_job(brokers: str) -> StreamExecutionEnvironment:
    env = StreamExecutionEnvironment.get_execution_environment()

    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(brokers)
        .set_topics(TICKS_TOPIC)
        .set_group_id("realtime-metrics-job")
        .set_starting_offsets(KafkaOffsetsInitializer.latest())
        .set_value_only_deserializer(Types.PICKLED_BYTE_ARRAY())
        .build()
    )
    sink = (
        KafkaSink.builder()
        .set_bootstrap_servers(brokers)
        .set_record_serializer(
            KafkaRecordSerializationSchema.builder()
            .set_topic(METRICS_TOPIC)
            .set_value_serialization_schema(lambda v: v)
            .build()
        )
        .build()
    )

    ticks = env.from_source(source, WatermarkStrategy.no_watermarks(), "ticks")
    (
        ticks
        .key_by(lambda raw: Tick.from_json(raw).stream_id)
        .window(TumblingProcessingTimeWindows.of(Time.seconds(WINDOW_SECONDS)))
        .process(MetricsWindowFunction())
        .sink_to(sink)
    )
    return env


if __name__ == "__main__":
    brokers = os.environ.get("REDPANDA_BROKERS", "redpanda:9092")
    env = build_job(brokers)
    env.execute("realtime-metrics-job")
