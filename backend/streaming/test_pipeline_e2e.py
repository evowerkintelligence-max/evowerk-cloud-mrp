# backend/streaming/test_pipeline_e2e.py
from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="set REDPANDA_BROKERS to run against a live Redpanda (needs Flink + PyFlink installed)",
)

# How many times to retry the full producer -> Flink job -> cache sequence
# before failing the test.
#
# KNOWN ISSUE (see task-7-report.md for the full investigation): on this
# Windows dev machine, PyFlink's *local* execution environment (used only
# here, to avoid needing a real Flink cluster for this test) intermittently
# fails its Python-UDF-worker bootstrap with a spurious
# "java.lang.IllegalStateException: Process died with exit code 0" (Beam's
# ProcessEnvironmentFactory misjudging its own worker-bootstrap subprocess as
# dead). This reproduces even in a fully standalone, synchronous run of this
# exact job with no test/app/asyncio code involved at all, so it is a
# PyFlink/Beam-on-Windows local-mode flakiness, not a bug in this pipeline's
# own code (Tasks 1-6) - and it does not affect production, which never runs
# the Flink job in this local/loopback mode at all (docker-compose.yml/
# flink.Dockerfile always run a real Flink cluster with persistent
# "process"-mode workers from the start). The failure rate was observed to
# climb sharply once app.services.realtime_cache.CacheConsumer (an
# AIOKafkaConsumer polling loop) runs concurrently in the same process, which
# this test's design requires - see the report for the full investigation and
# for how confident that correlation actually is. Retrying the whole attempt
# (fresh thread, fresh job, fresh stream_id) is a legitimate hedge for
# transient flakiness in general, but did not reliably get this test to pass
# in this specific sandboxed environment - see the report for the honest
# result of the final run.
MAX_ATTEMPTS = 5


def _run_flink_job_in_thread(brokers: str):
    from streaming.realtime_metrics_job import build_job

    env = build_job(brokers)
    # PyFlink's env.execute() blocks until the job is cancelled/fails; run it
    # in a daemon thread for the duration of this test only.
    env.execute("realtime-metrics-job-test")


async def _run_one_attempt(brokers: str) -> dict | None:
    from aiokafka import AIOKafkaProducer

    from app.services.realtime_cache import CacheConsumer
    from streaming.schemas import TICKS_TOPIC, Tick

    stream_id = f"e2e-{time.time()}"

    flink_thread = threading.Thread(
        target=_run_flink_job_in_thread, args=(brokers,), daemon=True,
    )
    flink_thread.start()
    await asyncio.sleep(5)  # give the job time to actually subscribe before publishing

    producer = AIOKafkaProducer(bootstrap_servers=brokers)
    await producer.start()
    try:
        for i in range(3):
            tick = Tick(
                stream_id=stream_id, org_id="", column_map={"units_col": "units"},
                row={"units": 10}, status="running", error=None,
                collected_at="2026-07-17T00:00:00+00:00",
            )
            await producer.send_and_wait(TICKS_TOPIC, key=stream_id.encode(), value=tick.to_json())
    finally:
        await producer.stop()

    cache = CacheConsumer(group_id=f"e2e-cache-{time.time()}")
    await cache.start()
    try:
        got = None
        for _ in range(20):  # 5s window + margin
            got = cache.get_snapshot(stream_id)
            if got is not None:
                break
            await asyncio.sleep(0.5)
        return got
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_full_pipeline_tick_to_cache_snapshot():
    brokers = os.environ["REDPANDA_BROKERS"]

    got = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        got = await _run_one_attempt(brokers)
        if got is not None:
            break
        print(f"[test_pipeline_e2e] attempt {attempt}/{MAX_ATTEMPTS} produced no snapshot, retrying")

    assert got is not None, (
        f"no metrics snapshot arrived within the window + margin after {MAX_ATTEMPTS} attempts"
    )
    assert got["metrics"]["throughput"] == 30.0
    assert got["rows_in_window"] == 3
    assert got["rows_collected"] == 3
