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

import glob
import os
import sys
from datetime import datetime, timezone

from pyflink.common import Time, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
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

# flink.Dockerfile ADDs the Kafka connector jar to /opt/flink/lib, already on
# the classpath for the dockerized cluster. A local StreamExecutionEnvironment
# (test_pipeline_e2e.py, run outside that image) has no such classpath, so
# fall back to a jar checked out alongside this file if one exists there.
_LOCAL_KAFKA_CONNECTOR_JARS = glob.glob(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "*.jar")
)


class MetricsWindowFunction(ProcessWindowFunction):
    def open(self, runtime_context: RuntimeContext):
        self.rows_collected_state = runtime_context.get_state(
            ValueStateDescriptor("rows_collected", Types.LONG())
        )
        self.started_at_state = runtime_context.get_state(
            ValueStateDescriptor("started_at", Types.STRING())
        )
        self.last_tick_at_state = runtime_context.get_state(
            ValueStateDescriptor("last_tick_at", Types.STRING())
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

        last_tick_at = self.last_tick_at_state.value()
        if ticks:
            latest_tick_at = max(t.collected_at for t in ticks)
            if last_tick_at is None or latest_tick_at > last_tick_at:
                last_tick_at = latest_tick_at
                self.last_tick_at_state.update(last_tick_at)

        last_tick_s = None
        if last_tick_at is not None:
            last_tick_s = round((now - datetime.fromisoformat(last_tick_at)).total_seconds(), 1)

        snap = MetricSnapshot(
            ts=now.isoformat(),
            stream_id=key,
            name=name,
            status=status,
            error=error,
            rows_in_window=len(rows),
            rows_collected=total_collected,
            duration_s=duration_s,
            last_tick_s=last_tick_s,
            metrics=metrics,
        )
        # SimpleStringSchema (source + sink below) moves Java Strings across
        # the Python/JVM bridge, not bytes - decode here to match, encode is
        # undone symmetrically by CacheConsumer/Tick.from_json on the other end.
        yield snap.to_json().decode("utf-8")


def build_job(brokers: str) -> StreamExecutionEnvironment:
    # PyFlink defaults local (non-cluster) runs to Beam's "loopback" Python
    # worker mode: a short-lived bootstrap subprocess hands off to a worker
    # thread inside this same process and exits by design. On this Windows
    # dev box that hand-off races Beam's own subprocess-aliveness check often
    # enough to intermittently fail job startup with a spurious "Process died
    # with exit code 0" (reproduced standalone, with no Flink/app code
    # involved - a PyFlink/Beam-on-Windows loopback-mode issue, not a bug in
    # this job). Forcing "process" mode - a real, persistent per-operator
    # Python worker subprocess, same as what a real (non-local) Flink cluster
    # always uses - sidesteps that race entirely. Must be set before
    # StreamExecutionEnvironment is constructed below (read once in its
    # __init__ via _open()).
    os.environ.setdefault("_python_worker_execution_mode", "process")
    env = StreamExecutionEnvironment.get_execution_environment()
    if _LOCAL_KAFKA_CONNECTOR_JARS:
        env.add_jars(*(f"file:///{p.replace(os.sep, '/')}" for p in _LOCAL_KAFKA_CONNECTOR_JARS))
    # Flink's Python UDF workers (used by ProcessWindowFunction below) shell
    # out to a "python" it finds itself unless told otherwise - bare `python`
    # on PATH may not be the interpreter this job is actually running under
    # (e.g. a dedicated venv). Pin it to sys.executable so the worker process
    # has the same pyflink/app imports as this process.
    env.set_python_executable(sys.executable)

    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(brokers)
        .set_topics(TICKS_TOPIC)
        .set_group_id("realtime-metrics-job")
        .set_starting_offsets(KafkaOffsetsInitializer.latest())
        # Types.PICKLED_BYTE_ARRAY() is a TypeInformation (how PyFlink pickles
        # values across the Python/JVM bridge) - not a DeserializationSchema
        # (how the JVM reads raw Kafka record bytes). SimpleStringSchema reads
        # each Kafka value as a UTF-8 string, matching Tick.to_json()'s bytes
        # (JSON is valid UTF-8) and what Tick.from_json/json.loads accept.
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )
    sink = (
        KafkaSink.builder()
        .set_bootstrap_servers(brokers)
        .set_record_serializer(
            KafkaRecordSerializationSchema.builder()
            .set_topic(METRICS_TOPIC)
            .set_value_serialization_schema(SimpleStringSchema())
            .build()
        )
        .build()
    )

    ticks = env.from_source(source, WatermarkStrategy.no_watermarks(), "ticks")
    (
        ticks
        .key_by(lambda raw: Tick.from_json(raw).stream_id)
        .window(TumblingProcessingTimeWindows.of(Time.seconds(WINDOW_SECONDS)))
        .process(MetricsWindowFunction(), output_type=Types.STRING())
        .sink_to(sink)
    )
    return env


if __name__ == "__main__":
    brokers = os.environ.get("REDPANDA_BROKERS", "redpanda:9092")
    env = build_job(brokers)
    env.execute("realtime-metrics-job")
