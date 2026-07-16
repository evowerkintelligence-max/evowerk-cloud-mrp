"""Real Flink job: consumes raw ticks from Redpanda's realtime.ticks topic,
computes the same windowed metrics tests/streaming_fakes.py's
FakeWindowingLoop computes for tests, and republishes to realtime.metrics.
Submitted separately from the FastAPI app - see docker-compose.yml's
flink-jobmanager service. Requires PyFlink
(backend/streaming/requirements.txt), not part of backend/requirements.txt.
The Kafka source/sink also need the flink-sql-connector-kafka jar on the
cluster's classpath (bundled in the official flink image's opt/, or add via
env.add_jars).

Fixed 5s tumbling window for every stream (not each stream's own
interval_seconds) - see the "Per-stream configurable window size" entry in
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md's Scope
section for why that's an explicit v1 simplification, not an oversight."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from pyflink.common import Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import KeyedProcessFunction, RuntimeContext, StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaRecordSerializationSchema, KafkaSink, KafkaSource
from pyflink.datastream.state import ValueStateDescriptor

from streaming.metrics_calc import compute_throughput_rate, compute_window_metrics, safe_float
from streaming.schemas import MetricMessage, TickMessage

TICKS_TOPIC = "realtime.ticks"
METRICS_TOPIC = "realtime.metrics"
WINDOW_SECONDS = 5


class WindowedMetrics(KeyedProcessFunction):
    """Keyed by stream_id. Buffers ticks in keyed state, flushes on a
    processing-time timer every WINDOW_SECONDS."""

    def open(self, ctx: RuntimeContext) -> None:
        self._buffer_state = ctx.get_state(ValueStateDescriptor("buffer", Types.STRING()))
        self._started_state = ctx.get_state(ValueStateDescriptor("started_at_ms", Types.LONG()))
        self._cumulative_units_state = ctx.get_state(ValueStateDescriptor("cumulative_units", Types.DOUBLE()))
        self._rows_collected_state = ctx.get_state(ValueStateDescriptor("rows_collected", Types.LONG()))
        self._timer_registered_state = ctx.get_state(ValueStateDescriptor("timer_registered", Types.BOOLEAN()))

    def process_element(self, value: str, ctx: "KeyedProcessFunction.Context"):
        buffered_raw = self._buffer_state.value()
        buffered = json.loads(buffered_raw) if buffered_raw else []
        buffered.append(value)
        self._buffer_state.update(json.dumps(buffered))

        if self._started_state.value() is None:
            self._started_state.update(int(time.time() * 1000))

        if not self._timer_registered_state.value():
            self._timer_registered_state.update(True)
            ctx.timer_service().register_processing_time_timer(
                ctx.timer_service().current_processing_time() + WINDOW_SECONDS * 1000
            )
        return []

    def on_timer(self, timestamp: int, ctx: "KeyedProcessFunction.OnTimerContext"):
        buffered_raw = self._buffer_state.value()
        buffered = json.loads(buffered_raw) if buffered_raw else []
        self._buffer_state.clear()

        if buffered:
            ticks = [TickMessage.from_json(b.encode("utf-8")) for b in buffered]
            rows = [t.row for t in ticks]
            column_map = ticks[-1].column_map
            metrics = compute_window_metrics(rows, column_map)

            units_col = column_map.get("units_col")
            cumulative = self._cumulative_units_state.value() or 0.0
            if units_col:
                cumulative += sum(safe_float(r.get(units_col, 0)) for r in rows)
                self._cumulative_units_state.update(cumulative)

            started_at_ms = self._started_state.value() or int(time.time() * 1000)
            duration_s = int((int(time.time() * 1000) - started_at_ms) / 1000)
            rate = compute_throughput_rate(cumulative, duration_s)
            if rate is not None:
                metrics["throughput_rate"] = rate

            rows_collected = (self._rows_collected_state.value() or 0) + len(rows)
            self._rows_collected_state.update(rows_collected)

            out = MetricMessage(
                stream_id=ticks[-1].stream_id,
                name=ticks[-1].name,
                status=ticks[-1].status,
                error=ticks[-1].error,
                rows_in_window=len(rows),
                rows_collected=rows_collected,
                duration_s=duration_s,
                last_tick_s=0.0,
                metrics=metrics,
                ts=datetime.now(timezone.utc).isoformat(),
            )
            yield out.to_json().decode("utf-8")

        # Re-arm so a stream with no new ticks this window still gets a
        # heartbeat window next cycle rather than falling silent forever.
        ctx.timer_service().register_processing_time_timer(timestamp + WINDOW_SECONDS * 1000)
        self._timer_registered_state.update(True)


def main() -> None:
    brokers = os.environ.get("REDPANDA_BROKERS", "redpanda:9092")

    env = StreamExecutionEnvironment.get_execution_environment()

    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(brokers)
        .set_topics(TICKS_TOPIC)
        .set_group_id("flink-realtime-metrics")
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

    ds = env.from_source(source, WatermarkStrategy.no_watermarks(), "realtime-ticks")
    keyed = ds.key_by(lambda raw: json.loads(raw)["stream_id"], key_type=Types.STRING())
    keyed.process(WindowedMetrics(), output_type=Types.STRING()).sink_to(sink)

    env.execute("realtime-metrics-job")


if __name__ == "__main__":
    main()
