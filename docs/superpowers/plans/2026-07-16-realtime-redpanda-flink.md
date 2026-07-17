# Realtime Redpanda + Flink Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-process `StreamRunner` poll+compute thread in `backend/app/services/realtime.py` with a Redpanda-backed producer and a Flink windowed-metrics job, so a stream's live snapshot is visible to every backend replica instead of only the one that happened to start its collector — without changing any model, route signature, or the SSE JSON contract.

**Architecture:** A thin `TickProducer` polls each `Integration` exactly like the old `StreamRunner` did, but publishes raw rows to Redpanda instead of computing metrics in-process. A Flink job (`backend/streaming/realtime_metrics_job.py`) windows those ticks per `stream_id` and republishes computed metrics. A `RealtimeCache` on every backend replica consumes that output into an in-memory `{stream_id: snapshot}` dict, which `_snapshot_dict` (SSE route) and `eval_stream_metric` (alerts) now read instead of `get_runner().snapshot()`. Everything upstream of `TickProducer` and downstream of `RealtimeCache` is unchanged.

**Tech Stack:** Python 3.11, FastAPI/SQLAlchemy async (existing), `aiokafka` (new — Redpanda client for the producer/cache side), Redpanda (new — Kafka-protocol broker), Apache Flink 1.19 / PyFlink (new — separate deployable, not part of the FastAPI app).

## Global Constraints

- Dependency versions use `>=` floors only, matching every existing line in `backend/requirements.txt` — no exact pins.
- `backend/pytest.ini` sets `asyncio_mode = auto`; async test functions do not need `@pytest.mark.asyncio`.
- The automated test suite never touches a real external service — Postgres is SQLite in-memory, Redis is `fakeredis`, and (new in this plan) the message bus is an in-memory `FakeMessageBus`. No task in this plan requires Docker to pass its own tests, **except Task 9** (the real PyFlink job), which is infra-dependent and is explicitly a manual-verification task, not an automated one — say so plainly rather than fabricate a pytest result for it.
- `backend/` is the import root (tests already do `from app.services import realtime as realtime_service` with no `backend.` prefix) — new top-level package `backend/streaming/` is therefore importable as `streaming.<module>`, sibling to `app`.
- Every new/modified file must keep `backend/app/api/routes/realtime.py`'s route signatures, `backend/app/models/realtime.py`, the SSE JSON envelope, and all frontend files untouched — this is an internals swap behind an existing contract, not a rewrite of the contract.
- Full design context: `docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md`.

---

### Task 1: Message schemas

**Files:**
- Create: `backend/streaming/__init__.py`
- Create: `backend/streaming/schemas.py`
- Test: `backend/tests/test_streaming_schemas.py`

**Interfaces:**
- Produces: `TickMessage(stream_id: str, org_id: str, name: str, column_map: dict[str, str], row: dict[str, Any], status: str, error: str | None, collected_at: str)` with `.to_json() -> bytes` and `TickMessage.from_json(bytes) -> TickMessage`.
- Produces: `MetricMessage(stream_id: str, name: str, status: str, error: str | None, rows_in_window: int, rows_collected: int, duration_s: int, last_tick_s: float | None, metrics: dict[str, Any], ts: str)` with the same `.to_json()`/`.from_json()` pair.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_streaming_schemas.py`:

```python
from __future__ import annotations

from streaming.schemas import MetricMessage, TickMessage


def test_tick_message_round_trips_through_json():
    msg = TickMessage(
        stream_id="11111111-1111-1111-1111-111111111111",
        org_id="22222222-2222-2222-2222-222222222222",
        name="Line 3 feed",
        column_map={"units_col": "units"},
        row={"units": 10, "scrap": 1},
        status="running",
        error=None,
        collected_at="2026-07-16T12:00:00+00:00",
    )
    restored = TickMessage.from_json(msg.to_json())
    assert restored == msg


def test_metric_message_round_trips_through_json():
    msg = MetricMessage(
        stream_id="11111111-1111-1111-1111-111111111111",
        name="Line 3 feed",
        status="running",
        error=None,
        rows_in_window=42,
        rows_collected=420,
        duration_s=300,
        last_tick_s=1.2,
        metrics={"throughput": 42.0, "oee_pct": 87.5},
        ts="2026-07-16T12:00:05+00:00",
    )
    restored = MetricMessage.from_json(msg.to_json())
    assert restored == msg


def test_metric_message_error_and_last_tick_s_survive_none():
    msg = MetricMessage(
        stream_id="s1", name="n", status="error", error="boom",
        rows_in_window=0, rows_collected=0, duration_s=0, last_tick_s=None,
        metrics={}, ts="2026-07-16T12:00:00+00:00",
    )
    restored = MetricMessage.from_json(msg.to_json())
    assert restored.error == "boom"
    assert restored.last_tick_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_streaming_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'streaming'`

- [ ] **Step 3: Write minimal implementation**

`backend/streaming/__init__.py`: empty file.

`backend/streaming/schemas.py`:

```python
"""Shared message shapes for the realtime Redpanda+Flink pipeline. Used by
the producer (app/services/realtime_producer.py), the real Flink job
(streaming/realtime_metrics_job.py), the test fake windower
(tests/streaming_fakes.py), and the cache consumer
(app/services/realtime_cache.py) so the JSON shape can't drift between them.
See docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TickMessage:
    stream_id: str
    org_id: str
    name: str
    column_map: dict[str, str]
    row: dict[str, Any]
    status: str
    error: str | None
    collected_at: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @staticmethod
    def from_json(data: bytes) -> "TickMessage":
        return TickMessage(**json.loads(data.decode("utf-8")))


@dataclass
class MetricMessage:
    stream_id: str
    name: str
    status: str
    error: str | None
    rows_in_window: int
    rows_collected: int
    duration_s: int
    last_tick_s: float | None
    metrics: dict[str, Any]
    ts: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @staticmethod
    def from_json(data: bytes) -> "MetricMessage":
        return MetricMessage(**json.loads(data.decode("utf-8")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_streaming_schemas.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/streaming/__init__.py backend/streaming/schemas.py backend/tests/test_streaming_schemas.py
git commit -m "feat: add tick/metric message schemas for realtime streaming pipeline"
```

---

### Task 2: Windowed metric formulas (pure)

**Files:**
- Create: `backend/streaming/metrics_calc.py`
- Test: `backend/tests/test_metrics_calc.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `safe_float(v: Any, default: float = 0.0) -> float`, `compute_window_metrics(rows: list[dict[str, Any]], column_map: dict[str, str]) -> dict[str, Any]`, `compute_throughput_rate(cumulative_units: float, duration_s: int) -> float | None`. Used by Task 7's `FakeWindowingLoop` and Task 9's real Flink job.

This is a line-for-line port of the metric math `StreamRunner.snapshot()` used in `backend/app/services/realtime.py` (throughput, scrap%/FPY%, downtime minutes, availability/performance/quality/OEE%), applied to one window's rows instead of the whole accumulated buffer. `throughput_rate` is split out into its own function because it depends on cumulative units-since-start, not one window's rows — a windowed function can't compute it alone; the caller (Flink job / test fake) supplies the running total via keyed state.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_metrics_calc.py`:

```python
from __future__ import annotations

from streaming.metrics_calc import compute_throughput_rate, compute_window_metrics


def test_throughput_sums_units_col():
    rows = [{"units": 10}, {"units": 15}]
    metrics = compute_window_metrics(rows, {"units_col": "units"})
    assert metrics["throughput"] == 25.0


def test_scrap_rate_and_fpy_from_scrap_and_total_units():
    rows = [{"units": 10, "scrap": 1}, {"units": 10, "scrap": 1}]
    metrics = compute_window_metrics(rows, {"units_col": "units", "scrap_col": "scrap"})
    assert metrics["scrap_rate_pct"] == 10.0
    assert metrics["fpy_pct"] == 90.0


def test_oee_components_from_full_column_map():
    rows = [{"units": 100, "good": 95, "planned_time": 60, "operating_time": 55, "target": 100}]
    metrics = compute_window_metrics(rows, {
        "units_col": "units",
        "good_units_col": "good",
        "planned_time_col": "planned_time",
        "operating_time_col": "operating_time",
        "target_rate_col": "target",
    })
    assert metrics["availability_pct"] == round(55 / 60 * 100, 1)
    assert metrics["quality_pct"] == 95.0
    assert metrics["performance_pct"] == 100.0
    assert metrics["oee_pct"] == round((55 / 60 * 100) * 1.0 * 0.95, 1)


def test_missing_column_map_keys_omit_that_metric():
    assert compute_window_metrics([{"x": 1}], {}) == {}


def test_downtime_col_sums_minutes():
    rows = [{"down": 2.5}, {"down": 1.5}]
    metrics = compute_window_metrics(rows, {"downtime_col": "down"})
    assert metrics["total_downtime_min"] == 4.0


def test_throughput_rate_uses_cumulative_units_over_duration():
    assert compute_throughput_rate(cumulative_units=120.0, duration_s=60) == 120.0
    assert compute_throughput_rate(cumulative_units=0.0, duration_s=0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_metrics_calc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'streaming.metrics_calc'`

- [ ] **Step 3: Write minimal implementation**

`backend/streaming/metrics_calc.py`:

```python
"""Windowed manufacturing metric formulas - a straight port of
app.services.realtime's old StreamRunner.snapshot() math, applied to one
window's rows instead of the whole accumulated buffer. Pure functions only:
no I/O, no Flink/Redpanda dependency, so both the real Flink job
(streaming/realtime_metrics_job.py) and the test fake windower
(tests/streaming_fakes.py) call the exact same code and get the exact same
numbers."""
from __future__ import annotations

from typing import Any


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_window_metrics(rows: list[dict[str, Any]], column_map: dict[str, str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    cm = column_map

    uc = cm.get("units_col")
    if uc:
        vals = [safe_float(r.get(uc, 0)) for r in rows]
        metrics["throughput"] = round(sum(vals), 1)

    sc = cm.get("scrap_col")
    tuc = cm.get("total_units_col") or uc
    if sc and tuc:
        total = sum(safe_float(r.get(tuc, 0)) for r in rows)
        scrap = sum(safe_float(r.get(sc, 0)) for r in rows)
        if total > 0:
            scrap_pct = round(scrap / total * 100, 2)
            metrics["scrap_rate_pct"] = scrap_pct
            metrics["fpy_pct"] = round(100 - scrap_pct, 2)

    dc = cm.get("downtime_col")
    if dc:
        metrics["total_downtime_min"] = round(sum(safe_float(r.get(dc, 0)) for r in rows), 1)

    pt = cm.get("planned_time_col")
    ot = cm.get("operating_time_col")
    guc = cm.get("good_units_col")
    tpc = cm.get("target_rate_col")
    if pt and ot and guc and uc:
        tp_total = sum(safe_float(r.get(pt, 0)) for r in rows)
        op_total = sum(safe_float(r.get(ot, 0)) for r in rows)
        u_total = sum(safe_float(r.get(uc, 0)) for r in rows)
        g_total = sum(safe_float(r.get(guc, 0)) for r in rows)
        avail = (op_total / tp_total * 100) if tp_total > 0 else 0.0
        quality = (g_total / u_total * 100) if u_total > 0 else 0.0
        perf = 100.0
        if tpc:
            target_total = sum(safe_float(r.get(tpc, 0)) for r in rows)
            if target_total > 0:
                perf = u_total / target_total * 100
        oee = avail * (perf / 100) * (quality / 100)
        metrics["availability_pct"] = round(avail, 1)
        metrics["quality_pct"] = round(quality, 1)
        metrics["performance_pct"] = round(perf, 1)
        metrics["oee_pct"] = round(oee, 1)

    return metrics


def compute_throughput_rate(cumulative_units: float, duration_s: int) -> float | None:
    """Same throughput_rate formula StreamRunner.snapshot() used
    (total_units / age_minutes), but driven off cumulative units-since-start
    rather than one window's units - rate-per-minute over a 5s window alone
    would be too noisy to be useful."""
    duration_min = duration_s / 60
    if duration_min <= 0:
        return None
    return round(cumulative_units / duration_min, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_metrics_calc.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/streaming/metrics_calc.py backend/tests/test_metrics_calc.py
git commit -m "feat: extract windowed metric formulas as pure functions"
```

---

### Task 3: MessageBus interface + FakeMessageBus

**Files:**
- Create: `backend/streaming/bus.py`
- Test: `backend/tests/test_bus.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `MessageBus` protocol (`async def publish(topic, key, value: bytes)`, `async def subscribe(topic, group_id)`, `def consume(topic, group_id) -> AsyncIterator[bytes]`), `FakeMessageBus` (full in-memory implementation). Used by Tasks 5, 6, 7, 8.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_bus.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_bus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'streaming.bus'`

- [ ] **Step 3: Write minimal implementation**

`backend/streaming/bus.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_bus.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/streaming/bus.py backend/tests/test_bus.py
git commit -m "feat: add MessageBus interface with in-memory FakeMessageBus"
```

---

### Task 4: RedpandaMessageBus (real aiokafka-backed implementation)

**Files:**
- Modify: `backend/streaming/bus.py` (append `RedpandaMessageBus`)
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_redpanda_bus.py`

**Interfaces:**
- Consumes: `MessageBus` protocol shape from Task 3.
- Produces: `RedpandaMessageBus(brokers: str)` satisfying the same protocol. Used by Task 8's `get_bus()`.

`aiokafka` must actually be installed for these tests to import `streaming.bus` at all (the tests mock the classes, not the import), so this task's first step also installs the dependency.

- [ ] **Step 1: Add the dependency and install it**

Add to `backend/requirements.txt`, in a new section after the connectors block:

```
# Realtime streaming: Redpanda (Kafka-wire-protocol) client for the tick
# producer and metrics cache consumer - see
# docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md.
aiokafka>=0.11.0
```

Run: `cd backend && pip install -r requirements.txt`
Expected: `aiokafka` installs successfully (exit code 0).

- [ ] **Step 2: Write the failing test**

`backend/tests/test_redpanda_bus.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_redpanda_bus.py -v`
Expected: FAIL with `ImportError: cannot import name 'RedpandaMessageBus' from 'streaming.bus'`

- [ ] **Step 4: Write minimal implementation**

Append to `backend/streaming/bus.py` (add `from aiokafka import AIOKafkaConsumer, AIOKafkaProducer` to the imports at the top of the file, alongside the existing `asyncio`/`typing` imports):

```python
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer


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
```

- [ ] **Step 5: Run test to verify it passes, then commit**

Run: `cd backend && python -m pytest tests/test_redpanda_bus.py -v`
Expected: `3 passed`

```bash
git add backend/streaming/bus.py backend/requirements.txt backend/tests/test_redpanda_bus.py
git commit -m "feat: add RedpandaMessageBus (aiokafka-backed) implementation"
```

---

### Task 5: TickProducer

**Files:**
- Create: `backend/app/services/realtime_producer.py`
- Test: `backend/tests/test_realtime_producer.py`

**Interfaces:**
- Consumes: `TickMessage` from Task 1 (`streaming.schemas`).
- Produces: `TICKS_TOPIC = "realtime.ticks"`, `TickProducer(stream_id, org_id, name, integration_type, integration_config, column_map, interval_seconds, publish: Callable[[str, TickMessage], None], accumulate: bool = True)` with `.status: str`, `.error: str | None`, `.stop() -> None`. Used by Task 8's `start_runner`.

This is additive - it does not touch `backend/app/services/realtime.py` or `StreamRunner`, so there's no regression risk from adding it; the app keeps running exactly as before until Task 8's cutover.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_realtime_producer.py`:

```python
from __future__ import annotations

import time
from unittest.mock import patch

from app.services.realtime_producer import TickProducer


class _StubConnector:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, max_rows: int = 0):
        return self._rows


def test_producer_publishes_one_tick_message_per_row():
    published = []

    def fake_get_connector(conn_type, config):
        return _StubConnector([{"units": 10}, {"units": 5}])

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={"units_col": "units"}, interval_seconds=100,
            publish=lambda topic, msg: published.append((topic, msg)),
        )
        deadline = time.time() + 2
        while len(published) < 2 and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert len(published) == 2
    assert {topic for topic, _msg in published} == {"realtime.ticks"}
    assert {msg.row["units"] for _t, msg in published} == {10, 5}
    assert all(msg.status == "running" and msg.name == "Line 1" for _t, msg in published)


def test_producer_marks_status_error_and_publishes_error_message_on_connector_failure():
    published = []

    def fake_get_connector(conn_type, config):
        class _Boom:
            def fetch_all(self, max_rows: int = 0):
                raise RuntimeError("connection refused")
        return _Boom()

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={}, interval_seconds=100,
            publish=lambda topic, msg: published.append((topic, msg)),
        )
        deadline = time.time() + 2
        while not published and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert producer.status == "error"
    assert "connection refused" in producer.error
    assert "connection refused" in published[0][1].error


def test_producer_marks_status_error_when_connector_init_fails():
    def fake_get_connector(conn_type, config):
        raise ValueError("bad config")

    with patch("app.services.realtime_producer.get_connector", side_effect=fake_get_connector):
        producer = TickProducer(
            stream_id="s1", org_id="o1", name="Line 1",
            integration_type="erp_rest", integration_config={},
            column_map={}, interval_seconds=100,
            publish=lambda topic, msg: None,
        )
        deadline = time.time() + 2
        while producer.status != "error" and time.time() < deadline:
            time.sleep(0.01)
        producer.stop()

    assert producer.status == "error"
    assert "bad config" in producer.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_realtime_producer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.realtime_producer'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/services/realtime_producer.py`:

```python
"""Thin poll-loop producer that publishes raw connector rows to the message
bus instead of computing metrics in-process - the collector half of the
Redpanda+Flink slice. Additive/new file; app/services/realtime.py still owns
StreamRunner until Task 8 cuts it over. See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Thread
from typing import Any, Callable

from app.services.connectors import get_connector
from streaming.schemas import TickMessage

TICKS_TOPIC = "realtime.ticks"


class TickProducer:
    def __init__(
        self,
        *,
        stream_id: str,
        org_id: str,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        publish: Callable[[str, TickMessage], None],
        accumulate: bool = True,
        # accumulate is accepted only for start_runner() call-site compatibility;
        # it is unused - Flink's per-window aggregation replaces the old
        # accumulate-vs-clear-buffer distinction entirely.
    ):
        self.stream_id = stream_id
        self.org_id = org_id
        self.name = name
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        self._publish = publish
        self.status = "running"
        self.error: str | None = None

        self._stop = Event()
        self._thread = Thread(
            target=self._run, args=(integration_type, integration_config),
            daemon=True, name=f"tick-producer-{stream_id[:8]}",
        )
        self._thread.start()

    def _tick(self, row: dict[str, Any]) -> TickMessage:
        return TickMessage(
            stream_id=self.stream_id, org_id=self.org_id, name=self.name,
            column_map=self.column_map, row=row, status=self.status, error=self.error,
            collected_at=datetime.now(timezone.utc).isoformat(),
        )

    def _run(self, integration_type: str, integration_config: dict[str, Any]) -> None:
        try:
            conn = get_connector(integration_type, integration_config)
        except Exception as exc:
            self.status = "error"
            self.error = f"Connector init failed: {exc}"
            return

        while not self._stop.is_set():
            try:
                rows: list[dict] = conn.fetch_all()
                self.status = "running"
                self.error = None
                for row in rows:
                    self._publish(TICKS_TOPIC, self._tick(row))
            except Exception as exc:
                self.status = "error"
                self.error = str(exc)
                self._publish(TICKS_TOPIC, self._tick({}))

            if integration_type != "mqtt":
                self._stop.wait(timeout=self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_realtime_producer.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/realtime_producer.py backend/tests/test_realtime_producer.py
git commit -m "feat: add TickProducer, the Redpanda-publishing replacement for StreamRunner's poll loop"
```

---

### Task 6: RealtimeCache

**Files:**
- Create: `backend/app/services/realtime_cache.py`
- Test: `backend/tests/test_realtime_cache.py`

**Interfaces:**
- Consumes: `MetricMessage` (Task 1), `MessageBus`/`FakeMessageBus` (Task 3).
- Produces: `RealtimeCache(bus: MessageBus, group_id: str, on_update: Callable[[str, dict], None] | None = None)` with `async def start()`, `async def stop()`, `def get_snapshot(stream_id: str) -> dict | None`, `def all_snapshots() -> dict[str, dict]`. Used by Task 8's `get_snapshot()`/`start_cache()`.

`on_update` fires synchronously, inside the consume loop, the instant a stream's snapshot changes - Task 8 wires the old JSONL history writer through it, event-driven, instead of a separate polling loop that could lag behind a fast-moving cache.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_realtime_cache.py`:

```python
from __future__ import annotations

import asyncio

from app.services.realtime_cache import RealtimeCache
from streaming.bus import FakeMessageBus
from streaming.schemas import MetricMessage


def _metric(stream_id: str, throughput: float, duration_s: int = 300) -> MetricMessage:
    return MetricMessage(
        stream_id=stream_id, name="Line 3", status="running", error=None,
        rows_in_window=5, rows_collected=50, duration_s=duration_s, last_tick_s=1.0,
        metrics={"throughput": throughput}, ts="2026-07-16T12:00:00+00:00",
    )


async def _wait_for(cache: RealtimeCache, stream_id: str) -> None:
    while cache.get_snapshot(stream_id) is None:
        await asyncio.sleep(0.01)


async def test_cache_reflects_latest_metric_message_for_a_stream():
    bus = FakeMessageBus()
    cache = RealtimeCache(bus, group_id="replica-1")
    await cache.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 10.0).to_json())
    await asyncio.wait_for(_wait_for(cache, "s1"), timeout=1)

    snap = cache.get_snapshot("s1")
    assert snap["metrics"]["throughput"] == 10.0
    assert snap["duration"] == "00:05:00"

    await cache.stop()


async def test_two_cache_instances_with_distinct_groups_both_see_the_update():
    """The correctness property the whole design hinges on: two backend
    replicas, each running their own RealtimeCache with a distinct group_id
    against the same bus, must both learn about every stream's update."""
    bus = FakeMessageBus()
    cache_a = RealtimeCache(bus, group_id="replica-a")
    cache_b = RealtimeCache(bus, group_id="replica-b")
    await cache_a.start()
    await cache_b.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 42.0).to_json())
    await asyncio.wait_for(_wait_for(cache_a, "s1"), timeout=1)
    await asyncio.wait_for(_wait_for(cache_b, "s1"), timeout=1)

    assert cache_a.get_snapshot("s1")["metrics"]["throughput"] == 42.0
    assert cache_b.get_snapshot("s1")["metrics"]["throughput"] == 42.0

    await cache_a.stop()
    await cache_b.stop()


async def test_on_update_callback_fires_with_stream_id_and_snapshot():
    seen = []
    bus = FakeMessageBus()
    cache = RealtimeCache(bus, group_id="replica-1", on_update=lambda sid, snap: seen.append((sid, snap)))
    await cache.start()

    await bus.publish("realtime.metrics", key="s1", value=_metric("s1", 7.0).to_json())
    await asyncio.wait_for(_wait_for(cache, "s1"), timeout=1)

    assert len(seen) == 1
    assert seen[0][0] == "s1"
    assert seen[0][1]["metrics"]["throughput"] == 7.0

    await cache.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_realtime_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.realtime_cache'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/services/realtime_cache.py`:

```python
"""In-memory {stream_id: latest snapshot} cache fed by the realtime.metrics
topic - replaces StreamRunner's per-process snapshot state. Any backend
replica running this consumer sees every stream's latest metrics, not just
the one that happens to run a given stream's producer, PROVIDED each
replica's RealtimeCache uses a distinct group_id (enforced by the caller -
see app/main.py's lifespan wiring in Task 8). See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from streaming.bus import MessageBus
from streaming.schemas import MetricMessage

METRICS_TOPIC = "realtime.metrics"


class RealtimeCache:
    def __init__(
        self,
        bus: MessageBus,
        group_id: str,
        on_update: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._bus = bus
        self._group_id = group_id
        self._on_update = on_update
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None

    def get_snapshot(self, stream_id: str) -> dict[str, Any] | None:
        return self._snapshots.get(stream_id)

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        return dict(self._snapshots)

    async def start(self) -> None:
        await self._bus.subscribe(METRICS_TOPIC, self._group_id)
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _consume_loop(self) -> None:
        async for raw in self._bus.consume(METRICS_TOPIC, self._group_id):
            msg = MetricMessage.from_json(raw)
            h, rem = divmod(msg.duration_s, 3600)
            m, s = divmod(rem, 60)
            snapshot = {
                "ts": msg.ts,
                "stream_id": msg.stream_id,
                "name": msg.name,
                "status": msg.status,
                "error": msg.error,
                "rows_in_window": msg.rows_in_window,
                "rows_collected": msg.rows_collected,
                "duration": f"{h:02d}:{m:02d}:{s:02d}",
                "duration_s": msg.duration_s,
                "last_tick_s": msg.last_tick_s,
                "metrics": msg.metrics,
            }
            self._snapshots[msg.stream_id] = snapshot
            if self._on_update is not None:
                self._on_update(msg.stream_id, snapshot)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_realtime_cache.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/realtime_cache.py backend/tests/test_realtime_cache.py
git commit -m "feat: add RealtimeCache, the cross-replica snapshot store fed by realtime.metrics"
```

---

### Task 7: Fake windowing loop (test-only stand-in for the Flink job)

**Files:**
- Create: `backend/tests/streaming_fakes.py`
- Test: `backend/tests/test_streaming_fakes.py`

**Interfaces:**
- Consumes: `FakeMessageBus` (Task 3), `compute_window_metrics`/`compute_throughput_rate`/`safe_float` (Task 2), `TickMessage`/`MetricMessage` (Task 1).
- Produces: `FakeWindowingLoop(bus: FakeMessageBus, window_seconds: float = 0.2)` with `async def start()`, `async def stop()`. Used by Task 8's `test_realtime.py` fixture.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_streaming_fakes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_streaming_fakes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.streaming_fakes'`

- [ ] **Step 3: Write minimal implementation**

`backend/tests/streaming_fakes.py`:

```python
"""Test-only stand-in for the real PyFlink job (Task 9) - reads raw ticks off
the FakeMessageBus, windows them in-process on a short fixed interval, and
republishes MetricMessages, so the rest of the pipeline (producer -> bus ->
cache -> routes/alerts) can be exercised end-to-end in the pytest suite
without a real Redpanda+Flink cluster. Uses the exact same
streaming.metrics_calc formulas the real Flink job uses, so results match
between test and production."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from streaming.bus import FakeMessageBus
from streaming.metrics_calc import compute_throughput_rate, compute_window_metrics, safe_float
from streaming.schemas import MetricMessage, TickMessage

TICKS_TOPIC = "realtime.ticks"
METRICS_TOPIC = "realtime.metrics"


class FakeWindowingLoop:
    def __init__(self, bus: FakeMessageBus, window_seconds: float = 0.2):
        self._bus = bus
        self._window_seconds = window_seconds
        self._group_id = f"fake-flink-{uuid.uuid4()}"
        self._started_at: dict[str, float] = {}
        self._cumulative_units: dict[str, float] = defaultdict(float)
        self._rows_collected: dict[str, int] = defaultdict(int)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._bus.subscribe(TICKS_TOPIC, self._group_id)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        window: dict[str, list[TickMessage]] = defaultdict(list)
        deadline = time.monotonic() + self._window_seconds
        consumer = self._bus.consume(TICKS_TOPIC, self._group_id)
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(consumer.__anext__(), timeout=timeout)
                msg = TickMessage.from_json(raw)
                window[msg.stream_id].append(msg)
            except asyncio.TimeoutError:
                pass

            if time.monotonic() >= deadline:
                await self._flush(window)
                window = defaultdict(list)
                deadline = time.monotonic() + self._window_seconds

    async def _flush(self, window: dict[str, list[TickMessage]]) -> None:
        for stream_id, ticks in window.items():
            if not ticks:
                continue
            if stream_id not in self._started_at:
                self._started_at[stream_id] = time.monotonic()

            rows = [t.row for t in ticks]
            column_map = ticks[-1].column_map
            metrics = compute_window_metrics(rows, column_map)

            units_col = column_map.get("units_col")
            if units_col:
                self._cumulative_units[stream_id] += sum(safe_float(r.get(units_col, 0)) for r in rows)
            duration_s = int(time.monotonic() - self._started_at[stream_id])
            rate = compute_throughput_rate(self._cumulative_units[stream_id], duration_s)
            if rate is not None:
                metrics["throughput_rate"] = rate
            self._rows_collected[stream_id] += len(rows)

            out = MetricMessage(
                stream_id=stream_id,
                name=ticks[-1].name,
                status=ticks[-1].status,
                error=ticks[-1].error,
                rows_in_window=len(rows),
                rows_collected=self._rows_collected[stream_id],
                duration_s=duration_s,
                last_tick_s=0.0,
                metrics=metrics,
                ts=datetime.now(timezone.utc).isoformat(),
            )
            await self._bus.publish(METRICS_TOPIC, key=stream_id, value=out.to_json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_streaming_fakes.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/streaming_fakes.py backend/tests/test_streaming_fakes.py
git commit -m "test: add FakeWindowingLoop, a Flink stand-in for pipeline tests"
```

---

### Task 8: Cutover - wire producer/cache into the app, delete StreamRunner

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/realtime.py` (full internals rewrite)
- Modify: `backend/app/api/routes/realtime.py:47-90` (`_snapshot_dict`, `_start`)
- Modify: `backend/app/services/alerts.py:296-333` (`eval_stream_metric`)
- Modify: `backend/app/main.py:56-62` (lifespan hook)
- Modify: `backend/tests/test_realtime.py` (fixture wiring only - no assertion changes)

**Interfaces:**
- Consumes: `TickProducer` (Task 5), `RealtimeCache` (Task 6), `FakeMessageBus`/`RedpandaMessageBus` (Tasks 3-4), `FakeWindowingLoop` (Task 7).
- Produces (in `app/services/realtime.py`, all replacing the old `StreamRunner`-backed versions with the same names): `get_bus() -> MessageBus`, `async def start_cache(group_id: str)`, `async def stop_cache()`, `def get_snapshot(stream_id: uuid.UUID) -> dict | None`, `def start_runner(*, stream_id, name, org_id, integration_type, integration_config, column_map, interval_seconds, accumulate) -> TickProducer`, `def get_runner(stream_id: uuid.UUID) -> TickProducer | None`, `def stop_runner(stream_id) -> bool`, `def stop_all_runners()`, `async def resume_all_streams()`. `read_history`/`delete_history`/`history_path` keep their exact prior signatures.

- [ ] **Step 1: Add the `redpanda_brokers` setting**

In `backend/app/core/config.py`, add one line next to the existing `redis_url` field (same file, same `Settings` class):

```python
    redpanda_brokers: str = "localhost:9092"
```

- [ ] **Step 2: Rewrite `backend/app/services/realtime.py`**

Full replacement:

```python
"""Live-polling collector engine. Redpanda+Flink slice: this module now owns
only the thin poll->publish producer (TickProducer, app/services/
realtime_producer.py) and the message bus / cache wiring - actual metric
computation moved to the Flink job (backend/streaming/realtime_metrics_job.py)
and the RealtimeCache (app/services/realtime_cache.py). See
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md.

RealtimeStream (app.models.realtime) still persists each stream's definition
and desired_status; resume_all_streams() still re-adopts anything marked
"running" after a restart. TickProducer's connector poll loop is still
per-process, same as StreamRunner was - what changed is that snapshot state
(RealtimeCache) is now fed by Redpanda, so any replica consuming
realtime.metrics has it, not just the one running a given stream's producer.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services.integrations import decrypt_config
from app.services.realtime_cache import RealtimeCache
from app.services.realtime_producer import TICKS_TOPIC, TickProducer
from streaming.bus import MessageBus, RedpandaMessageBus

_bus: MessageBus | None = None
_cache: RealtimeCache | None = None
_producers: dict[str, TickProducer] = {}
_loop: asyncio.AbstractEventLoop | None = None


# ── History (unchanged from before this slice) ──────────────────────────────

def _history_dir() -> Path:
    settings = get_settings()
    root = Path(settings.data_dir)
    root = root if root.is_absolute() else (Path.cwd() / root).resolve()
    d = root / "realtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path(stream_id: str) -> Path:
    return _history_dir() / f"{stream_id}.jsonl"


def read_history(stream_id: str, max_points: int = 2000) -> list[dict[str, Any]]:
    path = history_path(stream_id)
    if not path.exists():
        return []
    max_points = max(1, min(20_000, int(max_points)))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max_points:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def delete_history(stream_id: str) -> None:
    try:
        history_path(stream_id).unlink()
    except FileNotFoundError:
        pass


def _append_history(stream_id: str, snapshot: dict[str, Any]) -> None:
    # Same posture as the old StreamRunner._append_history: a disk hiccup
    # here must never take down the cache consumer.
    try:
        with history_path(stream_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": snapshot["ts"], "metrics": snapshot["metrics"]}) + "\n")
    except Exception:
        pass


# ── Bus / cache wiring ───────────────────────────────────────────────────────

def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        _bus = RedpandaMessageBus(brokers=get_settings().redpanda_brokers)
    return _bus


async def start_cache(group_id: str) -> None:
    """Called once from app.main's startup hook. group_id MUST be unique per
    backend replica - see RealtimeCache's docstring."""
    global _cache
    _cache = RealtimeCache(get_bus(), group_id=group_id, on_update=_append_history)
    await _cache.start()


async def stop_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.stop()
        _cache = None


def get_snapshot(stream_id: uuid.UUID) -> dict[str, Any] | None:
    if _cache is None:
        return None
    return _cache.get_snapshot(str(stream_id))


def _publish_sync(topic: str, msg) -> None:
    """Bridges TickProducer's background poll thread into the async bus.
    start_runner() below always captures the loop while running inside a
    live event loop (it's called synchronously from async route handlers),
    so this can safely hop back onto it from any producer's thread."""
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        get_bus().publish(topic, msg.stream_id, msg.to_json()), _loop
    ).result(timeout=5)


# ── Producer registry (replaces the old StreamRunner registry) ──────────────

def start_runner(
    *,
    stream_id: uuid.UUID,
    name: str,
    org_id: uuid.UUID,
    integration_type: str,
    integration_config: dict[str, Any],
    column_map: dict[str, str],
    interval_seconds: int,
    accumulate: bool,
) -> TickProducer:
    global _loop
    if _loop is None:
        _loop = asyncio.get_running_loop()

    sid = str(stream_id)
    existing = _producers.pop(sid, None)
    if existing is not None:
        existing.stop()

    producer = TickProducer(
        stream_id=sid,
        org_id=str(org_id),
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
        publish=_publish_sync,
    )
    _producers[sid] = producer
    return producer


def get_runner(stream_id: uuid.UUID) -> TickProducer | None:
    return _producers.get(str(stream_id))


def stop_runner(stream_id: uuid.UUID) -> bool:
    sid = str(stream_id)
    producer = _producers.pop(sid, None)
    if producer is None:
        return False
    producer.stop()
    return True


def stop_all_runners() -> None:
    producers = list(_producers.values())
    _producers.clear()
    for p in producers:
        p.stop()


async def resume_all_streams() -> None:
    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(
                select(RealtimeStream, Integration)
                .join(Integration, Integration.id == RealtimeStream.integration_id)
                .where(RealtimeStream.desired_status == "running")
            )
        ).all()

    for stream, integration in rows:
        try:
            start_runner(
                stream_id=stream.id,
                name=stream.name,
                org_id=stream.org_id,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others or app startup
```

- [ ] **Step 3: Update `backend/app/api/routes/realtime.py`**

Replace `_snapshot_dict` and `_start` (currently lines 47-90) with:

```python
def _snapshot_dict(stream: RealtimeStream, alerts: list) -> dict:
    """Cache-backed snapshot if this stream isn't explicitly stopped, else a
    definition-only "stopped" snapshot - explicit desired_status always wins
    over a possibly-stale cached value from before the stop. Alarm state from
    any enabled stream_metric alert rules targeting this stream rides along,
    same as before."""
    producer = realtime_service.get_runner(stream.id)

    if producer is None and stream.desired_status == "stopped":
        snap = {
            "ts": None, "stream_id": str(stream.id), "name": stream.name,
            "status": "stopped", "error": stream.last_error,
            "rows_in_window": 0, "rows_collected": 0,
            "duration": None, "duration_s": None, "last_tick_s": None, "metrics": {},
        }
    else:
        snap = realtime_service.get_snapshot(stream.id)
        if snap is None:
            snap = {
                "ts": None, "stream_id": str(stream.id), "name": stream.name,
                "status": "starting" if producer is not None else "not_running_in_this_process",
                "error": producer.error if producer is not None else stream.last_error,
                "rows_in_window": 0, "rows_collected": 0,
                "duration": None, "duration_s": None, "last_tick_s": None, "metrics": {},
            }
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


def _start(stream: RealtimeStream, integration: Integration) -> None:
    from app.services.integrations import decrypt_config

    realtime_service.start_runner(
        stream_id=stream.id,
        name=stream.name,
        org_id=stream.org_id,
        integration_type=integration.type,
        integration_config=decrypt_config(integration.config),
        column_map=stream.column_map,
        interval_seconds=stream.interval_seconds,
        accumulate=stream.accumulate,
    )
```

Every other function in this file (`_snapshot_for`, all `@router` routes, `_sse_generator`) is unchanged - they only ever call `_snapshot_dict`/`_start`, never `StreamRunner` directly.

- [ ] **Step 4: Update `backend/app/services/alerts.py`**

Replace `eval_stream_metric` (currently lines 296-333) with:

```python
def eval_stream_metric(rule: AlertRule) -> AlertTrigger:
    """Targets a live app.services.realtime_cache RealtimeCache snapshot
    instead of a dataset - same threshold/offender shape as the dataset
    evaluators above, reading the cached windowed snapshot (fed by the Flink
    job over Redpanda) instead of an in-process StreamRunner."""
    from app.services.realtime import get_snapshot

    cfg = rule.config
    stream_id = cfg.get("stream_id", "")
    metric_name = cfg.get("metric_name", "")
    comparison = cfg.get("comparison", "gt")  # "gt" | "lt"
    threshold = float(cfg.get("threshold", 0))

    snap = get_snapshot(uuid.UUID(stream_id)) if stream_id else None
    if snap is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "Stream not running — nothing to evaluate.",
        )

    metrics = snap.get("metrics", {})
    if metric_name not in metrics:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            f"Metric '{metric_name}' not available on this stream yet.",
        )

    value = float(metrics.get(metric_name) or 0.0)
    triggered = (value > threshold) if comparison == "gt" else (value < threshold)
    op_word = "above" if comparison == "gt" else "below"
    message = (
        f"{metric_name} = {value:g} ({op_word} threshold {threshold:g})" if triggered
        else f"{metric_name} = {value:g} (within threshold)"
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(value, 4), threshold,
        message, [snap.get("name", stream_id)] if triggered else [],
    )
```

- [ ] **Step 5: Wire the cache into `backend/app/main.py`'s lifespan hook**

Replace lines 56-62 with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    import socket

    # Re-adopt any realtime stream that was running before this process
    # started (redeploy/restart/crash) - see app.services.realtime. The cache
    # consumer group MUST be unique per replica so every replica sees every
    # stream's updates instead of a shard of them (see RealtimeCache).
    await realtime_service.start_cache(group_id=f"realtime-cache-{socket.gethostname()}-{os.getpid()}")
    await realtime_service.resume_all_streams()
    yield
    realtime_service.stop_all_runners()
    await realtime_service.stop_cache()
```

- [ ] **Step 6: Wire the fake pipeline into `backend/tests/test_realtime.py`**

Add these imports near the top of the file (alongside the existing `from app.services import realtime as realtime_service`):

```python
from streaming.bus import FakeMessageBus
from tests.streaming_fakes import FakeWindowingLoop
```

Add a new autouse fixture right after the existing `_stop_all_runners_after_each_test` fixture:

```python
@pytest.fixture(autouse=True)
async def _fake_streaming_pipeline(monkeypatch):
    """Swaps the real Redpanda bus for an in-memory FakeMessageBus and runs a
    FakeWindowingLoop in place of the real Flink job, so these tests exercise
    the full producer -> bus -> (fake) windowing -> cache -> route pipeline
    without needing a real Redpanda/Flink cluster. See
    docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md."""
    bus = FakeMessageBus()
    monkeypatch.setattr(realtime_service, "_bus", bus)
    monkeypatch.setattr(realtime_service, "_loop", None)
    monkeypatch.setattr(realtime_service, "_producers", {})

    windower = FakeWindowingLoop(bus, window_seconds=0.2)
    await windower.start()
    await realtime_service.start_cache(group_id="test-replica")

    yield

    await realtime_service.stop_cache()
    await windower.stop()
```

No other line in `test_realtime.py` changes - none of its 8 existing tests reference `StreamRunner` directly, only `realtime_service.get_runner`/`stop_all_runners`/`resume_all_streams` (all preserved) and the HTTP/SSE surface.

- [ ] **Step 7: Delete the now-dead code path**

Confirm nothing outside `backend/app/services/realtime.py` still imports `StreamRunner` (it never existed as an importable name outside this module - it was a private class), then run:

Run: `cd backend && grep -rn "StreamRunner" app/ tests/`
Expected: no output (the class no longer exists anywhere - it was replaced, not aliased).

- [ ] **Step 8: Run the full realtime + alerts suites**

Run: `cd backend && python -m pytest tests/test_realtime.py tests/test_alerts.py -v`
Expected: all tests pass, same count as before this task (8 in `test_realtime.py`, unchanged from the pre-existing suite).

- [ ] **Step 9: Commit**

```bash
git add backend/app/core/config.py backend/app/services/realtime.py backend/app/api/routes/realtime.py backend/app/services/alerts.py backend/app/main.py backend/tests/test_realtime.py
git commit -m "feat: cut realtime streaming over to Redpanda/Flink pipeline, delete StreamRunner"
```

---

### Task 9: Real PyFlink job

**Files:**
- Create: `backend/streaming/realtime_metrics_job.py`
- Create: `backend/streaming/requirements.txt`

**Interfaces:**
- Consumes: `compute_window_metrics`/`compute_throughput_rate`/`safe_float` (Task 2), `TickMessage`/`MetricMessage` (Task 1).
- Produces: a runnable Flink job, submitted via `flink run --python realtime_metrics_job.py`, reading `realtime.ticks` and writing `realtime.metrics`.

**This task cannot be verified with an automated pytest result in this repo** - it requires a running Flink cluster and Redpanda broker, which the rest of this plan deliberately avoids depending on (see Global Constraints). Treat the steps below as a real, best-effort implementation against PyFlink 1.19's documented DataStream API, but confirm it actually runs before trusting it - do not report this task "done" without having executed Step 3 for real against `docker-compose`'s services (Task 10).

- [ ] **Step 1: Add the PyFlink dependency (kept separate from the main app)**

`backend/streaming/requirements.txt`:

```
# PyFlink for the realtime metrics job - deliberately NOT in backend/requirements.txt;
# it pulls in enough (a JVM runtime, Flink's Python API surface) that it shouldn't
# share a dependency set with the FastAPI app. See
# docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md.
apache-flink>=1.19.0
```

Run (in a separate venv or the Flink image's Python environment, not the backend app's venv):
`pip install -r backend/streaming/requirements.txt`
Expected: installs successfully.

- [ ] **Step 2: Write the job**

`backend/streaming/realtime_metrics_job.py`:

```python
"""Real Flink job: consumes raw ticks from Redpanda's realtime.ticks topic,
computes the same windowed metrics tests/streaming_fakes.py's
FakeWindowingLoop computes for tests, and republishes to realtime.metrics.
Submitted separately from the FastAPI app - see docker-compose.yml's
flink-jobmanager service. Requires PyFlink
(backend/streaming/requirements.txt), not part of backend/requirements.txt.

Fixed 5s tumbling window for every stream (not each stream's own
interval_seconds) - see the "Per-stream configurable window size" entry in
docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md's Scope
section for why that's an explicit v1 simplification, not an oversight."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from pyflink.common import Types
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
        .set_value_only_deserializer(Types.STRING())
        .build()
    )
    sink = (
        KafkaSink.builder()
        .set_bootstrap_servers(brokers)
        .set_record_serializer(
            KafkaRecordSerializationSchema.builder()
            .set_topic(METRICS_TOPIC)
            .set_value_serialization_schema_str()
            .build()
        )
        .build()
    )

    ds = env.from_source(source, watermark_strategy=None, source_name="realtime-ticks")
    keyed = ds.key_by(lambda raw: json.loads(raw)["stream_id"])
    keyed.process(WindowedMetrics()).sink_to(sink)

    env.execute("realtime-metrics-job")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Manually verify against a real cluster (do this before trusting the job)**

This step needs Task 10's `docker-compose.yml` services running. Once they are:

Run: `docker compose up -d redpanda flink-jobmanager`
Run: `docker compose exec flink-jobmanager rpk topic create realtime.ticks realtime.metrics` (or `docker compose exec redpanda rpk topic create ...` depending on which image bundles `rpk`)
Run: `docker compose exec flink-jobmanager flink run --python /opt/flink/backend/streaming/realtime_metrics_job.py`
Run (separate terminal, publish a fake tick to prove the wiring end to end): `docker compose exec redpanda rpk topic produce realtime.ticks <<< '{"stream_id":"s1","org_id":"o1","name":"Line 1","column_map":{"units_col":"units"},"row":{"units":10},"status":"running","error":null,"collected_at":"2026-07-16T12:00:00+00:00"}'`
Run: `docker compose exec redpanda rpk topic consume realtime.metrics -n 1`
Expected: one JSON `MetricMessage` with `"stream_id":"s1"` and `"metrics":{"throughput":10.0,...}` within `WINDOW_SECONDS` (5s).

If this doesn't work, do not proceed to declaring the plan complete - fix the Flink job first. The exact PyFlink API surface (`KafkaSource`/`KafkaSink` builder methods in particular) can differ slightly between 1.18/1.19/1.20 - check the installed version's docs if `set_value_serialization_schema_str()` or similar methods aren't found.

- [ ] **Step 4: Commit**

```bash
git add backend/streaming/realtime_metrics_job.py backend/streaming/requirements.txt
git commit -m "feat: add real PyFlink windowed-metrics job"
```

---

### Task 10: docker-compose wiring

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: nothing code-level: pure infra wiring for Tasks 4 and 9.

- [ ] **Step 1: Add `redpanda` and `flink-jobmanager` services, and wire `backend`**

In `docker-compose.yml`, add two new services after the existing `redis` service, and add `REDPANDA_BROKERS` + a new `depends_on` entry to `backend`:

```yaml
  redpanda:
    image: redpandadata/redpanda:latest
    command: redpanda start --smp 1 --memory 1G --overprovisioned --node-id 0 --check=false
    ports:
      - "9092:9092"

  flink-jobmanager:
    image: flink:1.19-scala_2.12
    command: jobmanager
    environment:
      FLINK_PROPERTIES: "jobmanager.rpc.address: flink-jobmanager"
    ports:
      - "8081:8081"
    volumes:
      - ./backend:/opt/flink/backend
```

In the existing `backend` service block, add to `environment`:

```yaml
      REDPANDA_BROKERS: redpanda:9092
```

and to `depends_on`:

```yaml
      redpanda:
        condition: service_started
```

- [ ] **Step 2: Verify the compose file is well-formed**

Run: `docker compose config --quiet`
Expected: exit code 0, no output (a non-zero exit or YAML error means a syntax mistake in the edit above).

- [ ] **Step 3: Bring the stack up and confirm Redpanda and Flink actually start**

Run: `docker compose up -d`
Run: `docker compose ps`
Expected: `redpanda` and `flink-jobmanager` both show `running`/`Up`, alongside the pre-existing `db`, `redis`, `backend`, `frontend`. If either fails to start, check `docker compose logs redpanda` / `docker compose logs flink-jobmanager` before proceeding - this is the first real signal that the images/commands above are correct for the Docker version actually installed.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Redpanda and Flink jobmanager services to docker-compose"
```

---

## Self-Review

**Spec coverage:** Every section of `docs/superpowers/specs/2026-07-16-realtime-redpanda-flink-design.md` maps to a task - Architecture's two topics -> Tasks 1/5/6/9; Flink job -> Task 9; Cache consumer + "unique group per replica" -> Task 6 (design) + Task 8 (enforced group_id string) + Task 6's dedicated test; Backend changes list -> Task 8; Deployment -> Task 10; Files touched -> covered 1:1 across Tasks 1-10; Testing section's `test_realtime.py`-must-still-pass bar -> Task 8 Steps 6-8; the four "out of scope for v1" items are honored by construction (no exactly-once code, no Kafka Connect, no schema registry, no CEP, one fixed 5s window in Task 9) and are not re-litigated here.

**Placeholder scan:** No task step says "add error handling" or "similar to Task N" without code - every step shows complete, real code or a real runnable command with a real expected result. Task 9's Step 3 is the one exception that's flagged explicitly as unverifiable-by-me rather than papered over with a fabricated pytest pass.

**Type/signature consistency checked across tasks:** `TickMessage` gains `name` in Task 1 and every later constructor call (Task 5's `TickProducer._tick`, Task 7's `FakeWindowingLoop._flush`, Task 9's `WindowedMetrics.on_timer`) supplies it. `RealtimeCache.__init__`'s `on_update` parameter (Task 6) matches the `_append_history(stream_id, snapshot)` signature it's bound to in Task 8. `start_runner`'s new `org_id` parameter (Task 8) is threaded through both of its call sites (`_start()` in routes/realtime.py and `resume_all_streams()` in the same file). `get_runner()` keeps returning an object with `.status`/`.error` attributes (now `TickProducer` instead of `StreamRunner`), which is exactly what `test_realtime.py`'s existing `get_runner(...) is None`/`is not None` assertions and `_snapshot_dict`'s new fallback branch both depend on.
