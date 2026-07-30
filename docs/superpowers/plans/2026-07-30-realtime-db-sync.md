# Realtime DB Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Redpanda/Flink/SSE realtime pipeline; collectors write windowed samples straight to Postgres and the frontend syncs incrementally by cursor.

**Architecture:** One asyncio `StreamCollector` task per stream polls its Integration connector on a dedicated 1-thread executor, dedups rows against the previous poll (multiset diff), computes window metrics, and inserts one `realtime_samples` row per poll. The frontend polls `GET /realtime/streams/{id}/samples?since=<cursor>` at the stream interval and receives only rows it has not seen. Spec: `docs/superpowers/specs/2026-07-30-realtime-db-sync-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async (asyncpg prod, aiosqlite tests), Alembic, React + TanStack Query, pytest (asyncio_mode=auto), vitest.

## Global Constraints

- Backend tests run on in-memory SQLite: all SQL must be portable (no `DISTINCT ON`, no PG-only upsert). Cursor PK uses `BigInteger().with_variant(Integer, "sqlite")`.
- `pytest.ini` sets `asyncio_mode = auto`: async tests need no `@pytest.mark.asyncio` (existing files keep theirs; don't add new ones).
- Never use em dashes in any prose, comment, or doc you write. Preexisting app strings copied verbatim keep their original punctuation.
- Deliberate simplifications get a `ponytail:` comment naming the ceiling and upgrade path.
- Backend commands run from `backend/`: `python -m pytest tests/<file> -v`. Frontend from `frontend/`: `npm run build`, `npm run test`.
- Commit after every task. Pre-commit hooks (ruff, whitespace) run automatically; fix what they flag, never `--no-verify`. End commit messages with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Response envelope keys the frontend reads must keep their exact names: `ts, stream_id, name, status, error, rows_in_window, rows_collected, duration, duration_s, last_tick_s, metrics, alerts`.

---

### Task 1: RealtimeSample + RealtimeStreamMetric models and migration

**Files:**
- Modify: `backend/app/models/realtime.py` (append two models)
- Create: `backend/alembic/versions/a3e9c1f52d84_realtime_samples.py`
- Test: `backend/tests/test_realtime_sync.py` (new file)

**Interfaces:**
- Consumes: existing `RealtimeStream`, `Base`, `orgs`/`realtime_streams` tables.
- Produces: `RealtimeSample` (columns: `id: int` autoincrement cursor, `org_id`, `stream_id`, `ts`, `status`, `error`, `rows_in_window`, `rows_collected`, `duration_s`, `last_tick_s`, `metrics: dict`, `raw_rows: list`) and `RealtimeStreamMetric` (composite PK `stream_id` + `name`, `value`, `updated_at`). Later tasks import both from `app.models.realtime`. Also produces the test helpers `_register_and_login(client)`, `_seed_stream(db, *, machine=None, sensor_map=None)` reused by later test tasks in this file.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_realtime_sync.py`:

```python
"""Tests for the DB-backed realtime pipeline: sample model, collector
dedup, cursor sync, and retention. Replaces the broker-era cache/producer
tests."""
from __future__ import annotations

from sqlalchemy import select

from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream
from app.models.user import User

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _seed_stream(db, *, machine=None, sensor_map=None, interval_seconds=1):
    """ORM-level stream (desired_status=stopped so no collector task spawns).
    Caller must have registered an org already (via _register_and_login)."""
    owner = (await db.execute(select(User))).scalars().first()
    integration = Integration(
        org_id=owner.org_id, name="src", type="erp_rest", config={}, created_by=owner.id,
    )
    db.add(integration)
    await db.flush()
    stream = RealtimeStream(
        org_id=owner.org_id, integration_id=integration.id, name="Line 1",
        column_map={}, machine=machine, sensor_map=sensor_map or {},
        interval_seconds=interval_seconds, accumulate=True,
        desired_status="stopped", created_by=owner.id,
    )
    db.add(stream)
    await db.commit()
    await db.refresh(stream)
    return stream


async def test_sample_ids_autoincrement_and_filter_as_cursor(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)
        for i in range(3):
            db.add(RealtimeSample(
                org_id=stream.org_id, stream_id=stream.id,
                status="running", error=None,
                rows_in_window=1, rows_collected=i + 1, duration_s=i,
                last_tick_s=0.0, metrics={"throughput": float(i)}, raw_rows=[{"units": i}],
            ))
        await db.commit()

        ids = (
            await db.execute(
                select(RealtimeSample.id).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
        assert len(ids) == 3
        assert ids[0] < ids[1] < ids[2]  # monotonic cursor

        newer = (
            await db.execute(
                select(RealtimeSample)
                .where(RealtimeSample.stream_id == stream.id, RealtimeSample.id > ids[0])
                .order_by(RealtimeSample.id)
            )
        ).scalars().all()
        assert [s.rows_collected for s in newer] == [2, 3]
```

Note `ts` is omitted on purpose: the model must give it a default.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_realtime_sync.py -v`
Expected: FAIL with `ImportError: cannot import name 'RealtimeSample'`

- [ ] **Step 3: Add the models**

Append to `backend/app/models/realtime.py` (also extend the import line at the top of the file to `from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Uuid` and add `from typing import Any`):

```python
class RealtimeSample(Base):
    """One collector poll window, written by app.services.realtime.
    StreamCollector: the durable replacement for the old broker pipeline's
    windowed snapshot, the per-replica cache dict, AND the per-container
    JSONL history files. `id` is the incremental-sync cursor: the frontend
    passes back the highest id it has seen and only newer rows are returned,
    so nothing is ever re-sent."""

    __tablename__ = "realtime_samples"
    __table_args__ = (Index("ix_realtime_samples_stream_id_id", "stream_id", "id"),)

    # BigInteger in prod; plain INTEGER on SQLite so autoincrement works in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    rows_in_window: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_tick_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # This window's NEW rows (post-dedup), capped at 1000 by the collector.
    # Backs app.services.realtime.recent_rows for AutoML live scoring.
    raw_rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class RealtimeStreamMetric(Base):
    """Externally computed live metrics (today: AutoML risk scores) merged
    into a stream's snapshot metrics at read time. Replaces realtime_cache.
    set_external_metric. A separate row per (stream, name) with a composite
    PK so concurrent publishers upsert race-free instead of read-modify-
    writing a JSON blob."""

    __tablename__ = "realtime_stream_metrics"

    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    # Any + explicit JSON column: union annotations don't resolve in
    # SQLAlchemy's type map. Values are floats today (AutoML scores).
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

Also update the stale sentence in `RealtimeStream`'s docstring: replace the line
"`id` doubles as the stream_id used for the SSE route and the on-disk tick history file (app.services.realtime.history_path), so a resumed stream keeps its history contiguous."
with
"`id` doubles as the stream_id on RealtimeSample rows, so a resumed stream keeps its sample history contiguous."

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/a3e9c1f52d84_realtime_samples.py`:

```python
"""realtime samples + external stream metrics

Revision ID: a3e9c1f52d84
Revises: d5b0c4f936e2
Create Date: 2026-07-30 00:00:01.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a3e9c1f52d84"  # pragma: allowlist secret
down_revision = "d5b0c4f936e2"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_samples",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("rows_in_window", sa.Integer(), nullable=False),
        sa.Column("rows_collected", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("last_tick_s", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("raw_rows", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stream_id"], ["realtime_streams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_realtime_samples_org_id", "realtime_samples", ["org_id"])
    op.create_index("ix_realtime_samples_stream_id_id", "realtime_samples", ["stream_id", "id"])

    op.create_table(
        "realtime_stream_metrics",
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["stream_id"], ["realtime_streams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("stream_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("realtime_stream_metrics")
    op.drop_index("ix_realtime_samples_stream_id_id", table_name="realtime_samples")
    op.drop_index("ix_realtime_samples_org_id", table_name="realtime_samples")
    op.drop_table("realtime_samples")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_realtime_sync.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/realtime.py backend/alembic/versions/a3e9c1f52d84_realtime_samples.py backend/tests/test_realtime_sync.py
git commit -m "feat: realtime_samples + realtime_stream_metrics tables

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Move compute_window_metrics into app/services

**Files:**
- Create: `backend/app/services/stream_metrics.py` (moved from `backend/streaming/metrics.py`)
- Create: `backend/tests/test_stream_metrics.py` (moved from `backend/streaming/test_metrics.py`)
- Delete: `backend/streaming/metrics.py`, `backend/streaming/test_metrics.py`
- Modify: `backend/streaming/realtime_metrics_job.py:38` (import path, keeps the not-yet-deleted job importable)

**Interfaces:**
- Produces: `app.services.stream_metrics.compute_window_metrics(rows: list[dict], column_map: dict[str, str]) -> dict` used by Task 4. Function body is moved verbatim, zero logic changes.

- [ ] **Step 1: Move the files with git mv**

```bash
cd backend
git mv streaming/metrics.py app/services/stream_metrics.py
git mv streaming/test_metrics.py tests/test_stream_metrics.py
```

- [ ] **Step 2: Fix imports and docstrings**

In `backend/tests/test_stream_metrics.py`: change `from streaming.metrics import compute_window_metrics` to `from app.services.stream_metrics import compute_window_metrics`, and drop the stale `# backend/streaming/test_metrics.py` header comment.

In `backend/app/services/stream_metrics.py`: drop the `# backend/streaming/metrics.py` header comment and replace the first docstring paragraph with:

```python
"""Pure window-metric formulas (throughput/scrap/OEE/downtime), ported
unchanged from the old StreamRunner.snapshot(). Called once per collector
poll window by app.services.realtime.StreamCollector over that window's NEW
rows only (post-dedup), so absolute-sum fields report per-window activity.
Ratio fields (scrap_rate_pct, fpy_pct, oee_pct and friends) are normalized
and unaffected by window size."""
```

In `backend/streaming/realtime_metrics_job.py` change `from streaming.metrics import compute_window_metrics` to `from app.services.stream_metrics import compute_window_metrics` (the whole `streaming/` package is deleted in Task 7; this just keeps the interim tree importable).

- [ ] **Step 3: Run the moved tests plus the streaming schema tests**

Run: `cd backend && python -m pytest tests/test_stream_metrics.py streaming/test_schemas.py -v`
Expected: PASS (all)

- [ ] **Step 4: Commit**

```bash
git add -A backend/streaming backend/app/services/stream_metrics.py backend/tests/test_stream_metrics.py
git commit -m "refactor: move compute_window_metrics into app/services

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Extract sensor ingestion from SensorConsumer

**Files:**
- Create: `backend/app/services/sensor_ingest.py`
- Modify: `backend/app/services/sensor_consumer.py` (`_handle_tick` delegates)
- Create: `backend/tests/test_sensor_ingest.py` (ported from `tests/test_sensor_consumer.py`)
- Delete: `backend/tests/test_sensor_consumer.py`

**Interfaces:**
- Consumes: `RealtimeStream` (`.sensor_map`, `.machine`, `.org_id`), `SensorReading`/`SensorBaseline`/`SensorAnomaly`, `app.services.anomaly.score_reading`/`WINDOW`.
- Produces: `async ingest_rows(db: AsyncSession, stream: RealtimeStream, rows: list[dict]) -> None`. Writes readings/baselines/anomalies, does NOT commit (caller owns the transaction). No-op when `sensor_map` or `machine` is unset. Task 4's collector calls this.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sensor_ingest.py`. Same scenario as the old test_sensor_consumer (24 stable readings then a spike), but through `ingest_rows` directly, no Tick envelope:

```python
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorReading
from app.services.sensor_ingest import ingest_rows

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _make_sensor_stream(client, token) -> str:
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201, integ.text
    stream_res = await client.post(
        "/realtime/streams", headers=_h(token),
        json={
            "integration_id": integ.json()["id"], "name": "Line 1 sensors",
            "machine": "M1", "sensor_map": {"vib_x": "vibration_x_mm_s"},
        },
    )
    assert stream_res.status_code == 200, stream_res.text
    return stream_res.json()["stream_id"]


async def test_ingest_rows_writes_reading_and_flags_anomaly(client):
    token = await _register_and_login(client)
    stream_id = await _make_sensor_stream(client, token)

    # 24 stable readings to build the baseline, then one spike - mirrors
    # test_anomaly.py's out-of-control-spike case. Seeded jitter instead of
    # bit-identical values: 24 identical readings give mr_bar=0 and Nelson
    # rule 7 fires spuriously (see test_anomaly.py's stable-series case).
    random.seed(42)
    for i in range(24):
        value = round(10.0 + random.gauss(0, 0.15), 3)
        async with db_session.async_session_factory() as db:
            stream = (
                await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
            ).scalar_one()
            await ingest_rows(db, stream, [{"vibration_x_mm_s": value}])
            await db.commit()

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        await ingest_rows(db, stream, [{"vibration_x_mm_s": 50.0}])
        await db.commit()

        readings = (await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))).scalars().all()
        assert len(readings) == 25

        anomalies = (await db.execute(select(SensorAnomaly).where(SensorAnomaly.machine == "M1"))).scalars().all()
        assert len(anomalies) == 1
        assert 1 in anomalies[0].violations


async def test_ingest_rows_noop_without_sensor_map(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Plain", "type": "mqtt", "config": {"broker": "test", "topic": "#"}},
    )
    stream_res = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "No sensors"},
    )
    stream_id = stream_res.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        await ingest_rows(db, stream, [{"vibration_x_mm_s": 1.0}])
        await db.commit()
        readings = (await db.execute(select(SensorReading))).scalars().all()
        assert readings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sensor_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.sensor_ingest'`

- [ ] **Step 3: Write sensor_ingest.py**

Create `backend/app/services/sensor_ingest.py`. The loop body is `SensorConsumer._handle_tick`'s inner logic, moved verbatim, minus the Tick envelope and the commit:

```python
"""CBM sensor ingestion: extracts named sensor channels from a stream's
polled rows, persists SensorReading rows, refreshes the I-MR baseline, and
flags out-of-control readings via app.services.anomaly. Called by
app.services.realtime.StreamCollector inside the collector's per-window
transaction (the caller commits)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.anomaly import WINDOW, score_reading


async def ingest_rows(db: AsyncSession, stream: RealtimeStream, rows: list[dict]) -> None:
    if not stream.sensor_map or not stream.machine:
        return

    now = datetime.now(timezone.utc)
    for row in rows:
        for sensor, field in stream.sensor_map.items():
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            reading = SensorReading(
                org_id=stream.org_id, machine=stream.machine, sensor=sensor, value=value, ts=now,
            )
            db.add(reading)
            await db.flush()

            window_rows = (
                await db.execute(
                    select(SensorReading)
                    .where(
                        SensorReading.org_id == stream.org_id,
                        SensorReading.machine == stream.machine,
                        SensorReading.sensor == sensor,
                    )
                    .order_by(SensorReading.ts.desc())
                    .limit(WINDOW)
                )
            ).scalars().all()
            window_rows = list(reversed(window_rows))  # oldest first

            result = score_reading([r.value for r in window_rows], [r.ts for r in window_rows])

            baseline = (
                await db.execute(
                    select(SensorBaseline).where(
                        SensorBaseline.org_id == stream.org_id,
                        SensorBaseline.machine == stream.machine,
                        SensorBaseline.sensor == sensor,
                    )
                )
            ).scalar_one_or_none()
            if baseline is None:
                baseline = SensorBaseline(org_id=stream.org_id, machine=stream.machine, sensor=sensor)
                db.add(baseline)
            baseline.n = result.n
            baseline.centerline = result.centerline
            baseline.ucl = result.ucl
            baseline.lcl = result.lcl
            baseline.mr_bar = result.mr_bar

            if not result.learning and not result.in_control:
                db.add(SensorAnomaly(
                    org_id=stream.org_id, machine=stream.machine, sensor=sensor,
                    reading_id=reading.id, violations=result.violations,
                ))
```

- [ ] **Step 4: Make SensorConsumer delegate**

In `backend/app/services/sensor_consumer.py`, replace the entire body of `_handle_tick` (keep the signature) with:

```python
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
```

Add `from app.services.sensor_ingest import ingest_rows` to its imports and delete the now-unused imports (`datetime`/`timezone`, `SensorAnomaly`, `SensorBaseline`, `SensorReading`, `WINDOW`, `score_reading`). Delete `backend/tests/test_sensor_consumer.py` (`git rm backend/tests/test_sensor_consumer.py`); its scenario now lives in test_sensor_ingest.py and the whole consumer dies in Task 7.

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_sensor_ingest.py tests/test_cbm_api.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add -A backend/app/services/sensor_ingest.py backend/app/services/sensor_consumer.py backend/tests
git commit -m "refactor: extract sensor ingestion from the Kafka consumer

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: StreamCollector rewrite of app/services/realtime.py

**Files:**
- Modify: `backend/app/services/realtime.py` (full rewrite, temporary history shims kept)
- Modify: `backend/app/core/config.py:67` (add `realtime_retention_days`)
- Modify: `backend/app/api/routes/realtime.py` (await stop_runner, pass org_id)
- Modify: `backend/app/main.py:90` (`await realtime_service.stop_all_runners()`)
- Modify: `backend/dev_server.py:110-111` (patches stay await-compatible)
- Modify: `backend/tests/test_realtime.py:39-44` (autouse fixture awaits)
- Test: `backend/tests/test_realtime_sync.py` (extend)

**Interfaces:**
- Consumes: `RealtimeSample`, `RealtimeStreamMetric` (Task 1), `compute_window_metrics` (Task 2), `ingest_rows` (Task 3), `get_connector`, `decrypt_config`, `db_session.async_session_factory`.
- Produces (all in `app.services.realtime`; Tasks 5, 6, 8 depend on these exact signatures):
  - `class StreamCollector` with `.interval_seconds`, `.status`, `.error`, `start() -> None`, `async stop() -> None`, `async _poll_once() -> None`, `async _seed_counters() -> None`
  - `start_runner(*, stream_id: uuid.UUID, org_id: uuid.UUID, name: str, integration_type: str, integration_config: dict, column_map: dict, interval_seconds: int, accumulate: bool) -> StreamCollector` (sync; must be called with a running event loop)
  - `get_runner(stream_id) -> StreamCollector | None`
  - `async stop_runner(stream_id) -> bool`, `async stop_all_runners() -> None`, `async resume_all_streams() -> None`
  - `new_rows_since_last_poll(rows: list[dict], prev: Counter) -> tuple[list[dict], Counter]`
  - `async samples_since(db, stream_id: uuid.UUID, since: int | None, limit: int = 500) -> tuple[list[RealtimeSample], int | None]`
  - `sample_dict(sample: RealtimeSample) -> dict` (the snapshot envelope)
  - `async external_metrics(db, stream_id: uuid.UUID) -> dict[str, Any]`
  - `async latest_snapshot(db, stream: RealtimeStream) -> dict` (envelope + `name`, `interval_seconds`, stopped-status overlay, external-metric merge)
  - `async latest_snapshots_bulk(db, streams: list[RealtimeStream]) -> dict[str, dict]` (keyed by str(stream.id))
  - `async recent_rows(db, stream_id: uuid.UUID, n: int = 500) -> list[dict]`
  - `async set_external_metric(db, stream_id: uuid.UUID, name: str, value) -> None` (no commit)
  - `async _maybe_prune() -> None` and module global `_last_prune`
  - TEMPORARY (deleted in Task 7): `history_path`, `read_history`, `delete_history`, `_history_dir` kept verbatim so `realtime_cache.py` and the `/history` route keep importing until their own cutover.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_realtime_sync.py`:

```python
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.models.sensor import SensorReading
from app.services import realtime as realtime_service
from app.services.realtime import StreamCollector, new_rows_since_last_poll


def test_dedup_overlapping_polls_keeps_only_new_rows():
    prev = Counter()
    first, prev = new_rows_since_last_poll([{"a": 1}, {"a": 2}], prev)
    assert first == [{"a": 1}, {"a": 2}]
    second, prev = new_rows_since_last_poll([{"a": 1}, {"a": 2}, {"a": 3}], prev)
    assert second == [{"a": 3}]  # matching data is not copied


def test_dedup_is_multiset_not_set():
    # Two identical rows in ONE poll are two real rows (two units, no
    # distinguishing column) and both count.
    prev = Counter()
    rows, prev = new_rows_since_last_poll([{"a": 1}, {"a": 1}], prev)
    assert rows == [{"a": 1}, {"a": 1}]
    # Next poll adds one MORE identical row: only the extra one is new.
    rows, prev = new_rows_since_last_poll([{"a": 1}, {"a": 1}, {"a": 1}], prev)
    assert rows == [{"a": 1}]


def test_dedup_resets_when_source_empties():
    prev = Counter()
    _, prev = new_rows_since_last_poll([{"a": 1}], prev)
    _, prev = new_rows_since_last_poll([], prev)
    rows, prev = new_rows_since_last_poll([{"a": 1}], prev)
    assert rows == [{"a": 1}]  # source was cleared; the row is new again


class _StubConnector:
    """Feeds fetch_all one queued batch per call; repeats the last batch."""

    def __init__(self, batches):
        self.batches = list(batches)

    def fetch_all(self):
        if len(self.batches) > 1:
            return self.batches.pop(0)
        return self.batches[0]


def _collector_for(stream, batches) -> StreamCollector:
    c = StreamCollector(
        stream_id=stream.id, org_id=stream.org_id, name=stream.name,
        integration_type="erp_rest", integration_config={},
        column_map={"units_col": "units", "scrap_col": "scrap", "total_units_col": "units"},
        interval_seconds=1, accumulate=True,
    )
    c._conn = _StubConnector(batches)
    return c


async def test_poll_once_writes_sample_and_dedups_across_polls(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    c = _collector_for(stream, [
        [{"units": 10, "scrap": 1}, {"units": 20, "scrap": 2}],
        [{"units": 10, "scrap": 1}, {"units": 20, "scrap": 2}, {"units": 30, "scrap": 0}],
    ])
    await c._seed_counters()
    await c._poll_once()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        samples = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
    assert [s.rows_in_window for s in samples] == [2, 1]
    assert [s.rows_collected for s in samples] == [2, 3]
    assert samples[0].raw_rows == [{"units": 10, "scrap": 1}, {"units": 20, "scrap": 2}]
    assert samples[1].raw_rows == [{"units": 30, "scrap": 0}]
    assert samples[0].metrics["throughput"] == 30.0
    assert samples[0].metrics["scrap_rate_pct"] == 10.0
    assert samples[1].metrics["throughput"] == 30.0  # window 2 = only the new row
    assert samples[0].status == "running"


async def test_counters_seed_from_latest_sample_on_restart(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=5, rows_collected=41, duration_s=600,
            metrics={}, raw_rows=[],
        ))
        await db.commit()

    c = _collector_for(stream, [[{"units": 1, "scrap": 0}]])
    await c._seed_counters()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        latest = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id.desc())
            )
        ).scalars().first()
    assert latest.rows_collected == 42  # 41 carried over + 1 new
    assert latest.duration_s >= 600


async def test_connector_error_writes_error_sample_and_stream_last_error(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    class _Boom:
        def fetch_all(self):
            raise RuntimeError("source down")

    c = _collector_for(stream, [[]])
    c._conn = _Boom()
    await c._seed_counters()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        latest = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id.desc())
            )
        ).scalars().first()
        refreshed = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == stream.id))
        ).scalar_one()
    assert latest.status == "error"
    assert "source down" in latest.error
    assert refreshed.last_error == latest.error


async def test_poll_once_feeds_sensor_ingest(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db, machine="M1", sensor_map={"vib_x": "vibration_x_mm_s"})

    c = _collector_for(stream, [[{"vibration_x_mm_s": 10.0, "units": 1}]])
    await c._seed_counters()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        readings = (await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))).scalars().all()
    assert len(readings) == 1
    assert readings[0].value == 10.0


async def test_samples_since_returns_only_new_rows_and_advancing_cursor(client):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    c = _collector_for(stream, [
        [{"units": 1, "scrap": 0}],
        [{"units": 1, "scrap": 0}, {"units": 2, "scrap": 0}],
        [{"units": 1, "scrap": 0}, {"units": 2, "scrap": 0}, {"units": 3, "scrap": 0}],
    ])
    await c._seed_counters()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        first, cursor = await realtime_service.samples_since(db, stream.id, None)
        assert len(first) == 1  # no cursor: latest sample only, no backfill
        assert cursor == first[-1].id

    await c._poll_once()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        newer, cursor2 = await realtime_service.samples_since(db, stream.id, cursor)
        assert [s.rows_in_window for s in newer] == [1, 1]
        assert all(s.id > cursor for s in newer)
        assert cursor2 == newer[-1].id

        # Nothing new: empty page, cursor unchanged.
        empty, cursor3 = await realtime_service.samples_since(db, stream.id, cursor2)
        assert empty == []
        assert cursor3 == cursor2


async def test_prune_deletes_only_expired_samples(client, monkeypatch):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)
        old = RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            ts=datetime.now(timezone.utc) - timedelta(days=8),
            rows_in_window=0, rows_collected=0, duration_s=0, metrics={}, raw_rows=[],
        )
        fresh = RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=0, rows_collected=0, duration_s=0, metrics={}, raw_rows=[],
        )
        db.add_all([old, fresh])
        await db.commit()
        fresh_id = fresh.id

    # time.monotonic() can be under _PRUNE_EVERY_S on a freshly booted
    # machine, so 0.0 would not reliably force a prune; a huge negative does.
    monkeypatch.setattr(realtime_service, "_last_prune", -1e12)
    await realtime_service._maybe_prune()

    async with db_session.async_session_factory() as db:
        remaining = (
            await db.execute(select(RealtimeSample.id).where(RealtimeSample.stream_id == stream.id))
        ).scalars().all()
    assert remaining == [fresh_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_realtime_sync.py -v`
Expected: FAIL with `ImportError: cannot import name 'StreamCollector'`

- [ ] **Step 3: Rewrite backend/app/services/realtime.py**

Replace the entire file with:

```python
"""Realtime collection: one asyncio StreamCollector task per stream polls
the Integration's connector, dedups against the previous poll, computes
window metrics, and persists one RealtimeSample row per poll window.

Replaces the old broker pipeline (tick producer -> broker topics -> windowed
job -> per-replica cache consumer): samples land straight in Postgres and
every replica reads them from there, so there is no per-process snapshot
state and no message broker to provision.

The blocking connector I/O runs on a per-collector single-thread executor,
NOT asyncio.to_thread: MQTT connectors block continuously (collect_seconds
per fetch, no interval wait) and would starve the shared default executor
that app.services.integrations also uses.

RealtimeStream (app.models.realtime) persists each stream's definition and
desired_status; resume_all_streams() re-spins collectors on startup, so a
redeploy doesn't silently lose a running stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream, RealtimeStreamMetric
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config
from app.services.sensor_ingest import ingest_rows
from app.services.stream_metrics import compute_window_metrics

logger = logging.getLogger(__name__)

# Cap carried over from TickProducer's rows[-1000:] send cap.
_MAX_ROWS_PER_WINDOW = 1_000
_PRUNE_EVERY_S = 3600.0
_last_prune = 0.0


# ── Ingest dedup ─────────────────────────────────────────────────────────────

def _row_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def new_rows_since_last_poll(
    rows: list[dict[str, Any]], prev: Counter
) -> tuple[list[dict[str, Any]], Counter]:
    """Multiset difference of this poll against the previous one. Most
    connectors' fetch_all() returns the whole table every poll; only the
    rows that were not in the last poll are new. Multiset (not set)
    semantics keep genuinely duplicate rows within one poll.

    ponytail: two identical rows in ADJACENT polls with no distinguishing
    column are treated as re-fetched, and a process restart resets prev so
    the whole table re-ingests once. Upgrade path: persist a per-stream
    high-water column or row-hash set if either bites.
    """
    keys = [_row_key(r) for r in rows]
    cur = Counter(keys)
    remaining = dict(cur - prev)
    out: list[dict[str, Any]] = []
    # Walk in reverse: for append-only sources the new rows are at the end.
    for row, key in zip(reversed(rows), reversed(keys)):
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            out.append(row)
    out.reverse()
    return out, cur


# ── Collector ────────────────────────────────────────────────────────────────

class StreamCollector:
    """Polls one Integration's connector on an interval and writes one
    RealtimeSample per window. Construction is passive; start() schedules
    the loop on the running event loop (tests drive _poll_once directly)."""

    def __init__(
        self,
        *,
        stream_id: uuid.UUID,
        org_id: uuid.UUID,
        name: str,
        integration_type: str,
        integration_config: dict[str, Any],
        column_map: dict[str, str],
        interval_seconds: int,
        accumulate: bool,
    ):
        self.stream_id = stream_id
        self.org_id = org_id
        self.name = name
        self.integration_type = integration_type
        self.integration_config = integration_config
        self.column_map = column_map
        self.interval_seconds = max(1, interval_seconds)
        # Vestigial: still a RealtimeStream column and request field, but
        # metrics are always per-window now. Not read anywhere downstream.
        self.accumulate = accumulate
        self.status = "running"
        self.error: str | None = None

        self._conn: Any = None
        self._prev: Counter = Counter()
        self._rows_collected = 0
        self._duration_base = 0
        self._started = time.monotonic()
        self._last_rows_at: float | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"collector-{str(stream_id)[:8]}"
        )
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"collector-{str(self.stream_id)[:8]}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        else:
            self._executor.shutdown(wait=False)

    async def _run(self) -> None:
        try:
            try:
                self._conn = get_connector(self.integration_type, self.integration_config)
            except Exception as exc:
                self.status, self.error = "error", f"Connector init failed: {exc}"
                await self._write_sample([])  # surface the failure in snapshots
                return
            await self._seed_counters()
            while True:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("collector %s poll failed", self.stream_id)
                # MQTT blocks for collect_seconds inside fetch_all, so no
                # extra wait - except after an error, where the interval
                # doubles as backoff so a dead broker isn't a busy-loop.
                if self.integration_type != "mqtt" or self.status == "error":
                    await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            self._executor.shutdown(wait=False)

    async def _seed_counters(self) -> None:
        """Carry rows_collected/duration_s over from the stream's latest
        sample so the counters survive restarts."""
        async with db_session.async_session_factory() as db:
            latest = await _latest_sample(db, self.stream_id)
        if latest is not None:
            self._rows_collected = latest.rows_collected
            self._duration_base = latest.duration_s

    async def _poll_once(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.run_in_executor(self._executor, self._conn.fetch_all)
            self.status, self.error = "running", None
        except Exception as exc:
            self.status, self.error = "error", str(exc)
            rows = []

        if self.status == "running":
            new_rows, self._prev = new_rows_since_last_poll(rows, self._prev)
            new_rows = new_rows[-_MAX_ROWS_PER_WINDOW:]
        else:
            new_rows = []  # keep _prev: a transient fetch error must not re-ingest the table
        await self._write_sample(new_rows)
        await _maybe_prune()

    async def _write_sample(self, new_rows: list[dict[str, Any]]) -> None:
        now_mono = time.monotonic()
        if new_rows:
            self._last_rows_at = now_mono
        self._rows_collected += len(new_rows)
        sample = RealtimeSample(
            org_id=self.org_id,
            stream_id=self.stream_id,
            status=self.status,
            error=self.error,
            rows_in_window=len(new_rows),
            rows_collected=self._rows_collected,
            duration_s=self._duration_base + int(now_mono - self._started),
            last_tick_s=(
                round(now_mono - self._last_rows_at, 1) if self._last_rows_at is not None else None
            ),
            metrics=compute_window_metrics(new_rows, self.column_map),
            raw_rows=new_rows,
        )
        async with db_session.async_session_factory() as db:
            db.add(sample)
            stream = await db.get(RealtimeStream, self.stream_id)
            if stream is not None:
                if stream.last_error != self.error:
                    stream.last_error = self.error
                if new_rows:
                    await ingest_rows(db, stream, new_rows)
            await db.commit()


async def _maybe_prune() -> None:
    """Global retention sweep, at most hourly per process, fired by whichever
    collector polls first past the hour mark. Global (not per stream) so
    stopped streams' rows are pruned too."""
    global _last_prune
    if time.monotonic() - _last_prune < _PRUNE_EVERY_S:
        return
    _last_prune = time.monotonic()
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().realtime_retention_days)
    async with db_session.async_session_factory() as db:
        await db.execute(delete(RealtimeSample).where(RealtimeSample.ts < cutoff))
        await db.commit()


# ── Reads (routes, alerts, AutoML) ───────────────────────────────────────────

def _fmt_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def sample_dict(sample: RealtimeSample) -> dict[str, Any]:
    """The snapshot envelope frontend/src/pages/Realtime.tsx reads - same
    keys the old SSE feed carried."""
    return {
        "ts": sample.ts.isoformat() if sample.ts else None,
        "stream_id": str(sample.stream_id),
        "status": sample.status,
        "error": sample.error,
        "rows_in_window": sample.rows_in_window,
        "rows_collected": sample.rows_collected,
        "duration": _fmt_duration(sample.duration_s),
        "duration_s": sample.duration_s,
        "last_tick_s": sample.last_tick_s,
        "metrics": dict(sample.metrics or {}),
    }


async def _latest_sample(db: AsyncSession, stream_id: uuid.UUID) -> RealtimeSample | None:
    return (
        await db.execute(
            select(RealtimeSample)
            .where(RealtimeSample.stream_id == stream_id)
            .order_by(RealtimeSample.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def samples_since(
    db: AsyncSession, stream_id: uuid.UUID, since: int | None, limit: int = 500
) -> tuple[list[RealtimeSample], int | None]:
    """Incremental sync: rows with id > since, oldest first. No `since`
    returns just the latest sample (cheap page-open, no backfill). The
    returned cursor is the highest id RETURNED (not the table max), so a
    truncated page self-heals on the next poll."""
    limit = max(1, min(2000, int(limit)))
    if since is None:
        latest = await _latest_sample(db, stream_id)
        rows = [latest] if latest is not None else []
    else:
        rows = list(
            (
                await db.execute(
                    select(RealtimeSample)
                    .where(RealtimeSample.stream_id == stream_id, RealtimeSample.id > int(since))
                    .order_by(RealtimeSample.id)
                    .limit(limit)
                )
            ).scalars()
        )
    cursor = rows[-1].id if rows else since
    return rows, cursor


async def external_metrics(db: AsyncSession, stream_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id == stream_id))
    ).scalars().all()
    return {m.name: m.value for m in rows}


def _assemble_snapshot(
    stream: RealtimeStream, sample: RealtimeSample | None, external: dict[str, Any]
) -> dict[str, Any]:
    if sample is None:
        snap: dict[str, Any] = {
            "ts": None, "stream_id": str(stream.id), "name": stream.name,
            "status": "stopped" if stream.desired_status == "stopped" else "starting",
            "error": stream.last_error,
            "rows_in_window": 0, "rows_collected": 0,
            "duration": None, "duration_s": None, "last_tick_s": None, "metrics": {},
        }
    else:
        snap = sample_dict(sample)
        snap["name"] = stream.name
        # A stopped stream keeps its real final counters/metrics, but the
        # reported status must reflect the user's stop, not the last write.
        if stream.desired_status == "stopped":
            snap["status"] = "stopped"
    if external:
        snap["metrics"] = {**snap["metrics"], **external}
    snap["interval_seconds"] = stream.interval_seconds
    return snap


async def latest_snapshot(db: AsyncSession, stream: RealtimeStream) -> dict[str, Any]:
    sample = await _latest_sample(db, stream.id)
    external = await external_metrics(db, stream.id)
    return _assemble_snapshot(stream, sample, external)


async def latest_snapshots_bulk(
    db: AsyncSession, streams: list[RealtimeStream]
) -> dict[str, dict[str, Any]]:
    """latest_snapshot for N streams in two queries instead of 2N.
    Portable latest-per-stream (works on the SQLite test DB, no DISTINCT ON)."""
    if not streams:
        return {}
    ids = [s.id for s in streams]
    # max(id) per stream, then fetch those rows.
    max_ids = (
        select(func.max(RealtimeSample.id))
        .where(RealtimeSample.stream_id.in_(ids))
        .group_by(RealtimeSample.stream_id)
    )
    samples = (
        await db.execute(select(RealtimeSample).where(RealtimeSample.id.in_(max_ids)))
    ).scalars().all()
    by_stream = {str(s.stream_id): s for s in samples}
    ext_rows = (
        await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id.in_(ids)))
    ).scalars().all()
    ext_by_stream: dict[str, dict[str, Any]] = {}
    for m in ext_rows:
        ext_by_stream.setdefault(str(m.stream_id), {})[m.name] = m.value
    return {
        str(s.id): _assemble_snapshot(s, by_stream.get(str(s.id)), ext_by_stream.get(str(s.id), {}))
        for s in streams
    }


async def recent_rows(db: AsyncSession, stream_id: uuid.UUID, n: int = 500) -> list[dict[str, Any]]:
    """Last `n` ingested raw rows for a stream, oldest first - flattened
    from the newest non-empty samples. The actual feature columns AutoML
    scores on, not the windowed aggregates."""
    n = max(1, int(n))
    samples = (
        await db.execute(
            select(RealtimeSample.raw_rows)
            .where(RealtimeSample.stream_id == stream_id, RealtimeSample.rows_in_window > 0)
            .order_by(RealtimeSample.id.desc())
            .limit(n)  # every non-empty sample has >= 1 row, so n samples cover n rows
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for raw in samples:  # newest sample first
        out = list(raw) + out
        if len(out) >= n:
            break
    return out[-n:]


async def set_external_metric(db: AsyncSession, stream_id: uuid.UUID, name: str, value: Any) -> None:
    """Upsert one (stream, name) external metric row. Caller commits."""
    await db.merge(
        RealtimeStreamMetric(
            stream_id=stream_id, name=name, value=value, updated_at=datetime.now(timezone.utc)
        )
    )


# ── TEMPORARY JSONL history shims (deleted in the broker-stack removal task;
# realtime_cache.py and the /history route still import these until then) ────

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


# ── Global registry (this process only) ──────────────────────────────────────

_runners: dict[str, StreamCollector] = {}


def start_runner(
    *,
    stream_id: uuid.UUID,
    org_id: uuid.UUID,
    name: str,
    integration_type: str,
    integration_config: dict[str, Any],
    column_map: dict[str, str],
    interval_seconds: int,
    accumulate: bool,
) -> StreamCollector:
    """(Re)start the collector for an existing stream_id. Idempotent: any
    prior collector for the same id is cancelled without being awaited
    (fire-and-forget; its finally block shuts its executor down)."""
    sid = str(stream_id)
    existing = _runners.pop(sid, None)
    if existing is not None and existing._task is not None:
        existing._task.cancel()

    collector = StreamCollector(
        stream_id=stream_id,
        org_id=org_id,
        name=name,
        integration_type=integration_type,
        integration_config=integration_config,
        column_map=column_map,
        interval_seconds=interval_seconds,
        accumulate=accumulate,
    )
    collector.start()
    _runners[sid] = collector
    return collector


def get_runner(stream_id: uuid.UUID) -> StreamCollector | None:
    return _runners.get(str(stream_id))


async def stop_runner(stream_id: uuid.UUID) -> bool:
    runner = _runners.pop(str(stream_id), None)
    if runner is None:
        return False
    await runner.stop()
    return True


async def stop_all_runners() -> None:
    """Called from app.main's shutdown hook so collector tasks don't get
    silently orphaned when the event loop exits."""
    runners = list(_runners.values())
    _runners.clear()
    for r in runners:
        await r.stop()


async def resume_all_streams() -> None:
    """Called from app.main's startup hook. Re-spins the collector for every
    RealtimeStream still marked desired_status="running" across all orgs, so
    a redeploy picks running streams back up."""
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
                org_id=stream.org_id,
                name=stream.name,
                integration_type=integration.type,
                integration_config=decrypt_config(integration.config),
                column_map=stream.column_map,
                interval_seconds=stream.interval_seconds,
                accumulate=stream.accumulate,
            )
        except Exception:
            continue  # best-effort resume; a bad integration shouldn't block the others
```

Note the old module's `_safe_float`, `TickProducer`, `Lock`/`Thread` imports, and `kafka`/`streaming.schemas` imports are all gone.

- [ ] **Step 4: Add the retention setting**

In `backend/app/core/config.py`, directly below the `redpanda_brokers` line (line 67), add:

```python
    # How long realtime_samples rows are kept. One row per stream per poll
    # window (a 5s stream writes ~17k rows/day), pruned hourly by whichever
    # collector polls first past the hour mark (see app.services.realtime).
    realtime_retention_days: int = 7
```

- [ ] **Step 5: Sync the callers that Task 4 breaks**

`backend/app/api/routes/realtime.py`:
- In `_start`, add `org_id=stream.org_id` to the `realtime_service.start_runner(...)` call (keyword args, after `stream_id=stream.id`). `_start`'s signature stays sync; `start_runner` is still a sync call.
- In `stop_stream`: `realtime_service.stop_runner(stream.id)` becomes `await realtime_service.stop_runner(stream.id)`.
- In `delete_stream`: same `await` change.

`backend/app/main.py` line 90: `realtime_service.stop_all_runners()` becomes `await realtime_service.stop_all_runners()`.

`backend/dev_server.py` line 111: `realtime_service.stop_all_runners = lambda: None` becomes `realtime_service.stop_all_runners = _noop` (the async `_noop` defined just above it; the lifespan awaits this now).

`backend/tests/test_realtime.py` lines 39-44, the autouse fixture becomes async:

```python
@pytest.fixture(autouse=True)
async def _stop_all_runners_after_each_test():
    """Streams are real background tasks registered in module-level state -
    without this, a collector from one test could keep polling into the next."""
    yield
    await realtime_service.stop_all_runners()
```

- [ ] **Step 6: Run the new tests, then the touched suites**

Run: `cd backend && python -m pytest tests/test_realtime_sync.py -v`
Expected: PASS (all, including Task 1's test)

Run: `cd backend && python -m pytest tests/test_realtime.py tests/test_sensor_ingest.py tests/test_stream_metrics.py -v`
Expected: PASS. The old test_realtime tests still pass because the snapshot routes still read realtime_cache (cutover is Task 5); collectors created by route tests now write real samples, which nothing yet asserts on.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/realtime.py backend/app/core/config.py backend/app/api/routes/realtime.py backend/app/main.py backend/dev_server.py backend/tests/test_realtime.py backend/tests/test_realtime_sync.py
git commit -m "feat: StreamCollector writes deduped window samples to Postgres

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Routes cutover: /samples endpoint, DB-backed snapshots, SSE deleted

**Files:**
- Modify: `backend/app/api/routes/realtime.py` (full rewrite)
- Modify: `backend/tests/test_realtime.py` (rewrite affected tests)

**Interfaces:**
- Consumes: `latest_snapshot`, `latest_snapshots_bulk`, `samples_since`, `sample_dict`, `external_metrics` from Task 4; `alerts_service.stream_alerts` / `stream_alerts_bulk` (still cache-based until Task 6, signatures unchanged).
- Produces: `GET /realtime/streams/{id}/samples?since=&limit=` returning `{"stream_id", "cursor", "samples": [envelope...]}` with external metrics merged into the NEWEST returned sample. Snapshot payloads now include `interval_seconds` and use status `starting` instead of `not_running_in_this_process`. Task 8's frontend consumes exactly this.

- [ ] **Step 1: Rewrite the failing tests first**

In `backend/tests/test_realtime.py`:

1. Delete the module-level `import os`, `from app.services import realtime_cache` import, and the whole `_cache_consumer` fixture (lines 47-61).
2. Delete both `@pytest.mark.skipif(...)` decorators (the tests now run everywhere; that is the point of this change).
3. In `test_stream_collects_and_computes_metrics`, replace the poll-comment block (lines 118-120) with `# Give the background collector time to write its first sample.` (loop unchanged), then replace everything from `assert snap["status"] == "running"` to the end of the test with:

```python
    assert snap["status"] == "running"
    assert snap["rows_collected"] > 0

    # The fake REST server returns the same single row every poll: the first
    # window ingests it, later windows dedup to empty. Metrics therefore live
    # in the first non-empty history point, not necessarily the latest sample.
    history = (await client.get(f"/realtime/streams/{stream_id}/history", headers=headers)).json()
    assert len(history["points"]) > 0
    first_window = next(p for p in history["points"] if p["metrics"].get("throughput"))
    assert first_window["metrics"]["throughput"] > 0
    assert first_window["metrics"]["scrap_rate_pct"] == pytest.approx(10.0)
```
4. Replace `test_stop_then_start_resumes_collection`'s trailing comment block (lines 162-166) with `# The /start response reflects the DB state at that instant; the first sample lands within one interval.` (loop unchanged).
5. Replace `test_stopped_stream_with_stale_cache_reports_stopped_and_real_name` entirely with:

```python
async def test_stopped_stream_keeps_final_counters_and_reports_stopped(client, fake_rest_server):
    """A stopped stream reports its real final counters from its last
    persisted sample, with status overlaid to 'stopped' and name from the
    stream row (samples don't carry the human-readable name)."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "name": "Line 9", "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    # Seed a persisted sample directly (deterministic, no timing dependence).
    from app.db import session as db_session
    from app.models.realtime import RealtimeSample, RealtimeStream
    from sqlalchemy import select

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=3, rows_collected=3, duration_s=5, last_tick_s=1.0,
            metrics={"throughput": 1.0}, raw_rows=[],
        ))
        await db.commit()

    stop = await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    assert stop.json()["name"] == "Line 9"

    snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
    assert snap["status"] == "stopped"
    assert snap["name"] == "Line 9"
    assert snap["rows_collected"] == 3  # real final counters, not a zeroed shell
    assert snap["metrics"]["throughput"] == 1.0
```

6. Replace `test_sse_generator_yields_first_snapshot_frame` entirely with:

```python
async def test_samples_endpoint_returns_only_new_rows_per_cursor(client, fake_rest_server):
    """The incremental-sync contract: a client that passes back the cursor
    it last received gets only samples written since, never a duplicate."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {"units_col": "units"}},
    )
    stream_id = create.json()["stream_id"]

    # Wait for the first persisted sample.
    for _ in range(40):
        first = (await client.get(f"/realtime/streams/{stream_id}/samples", headers=headers)).json()
        if first["samples"]:
            break
        await asyncio.sleep(0.3)
    assert first["samples"], "collector never wrote a sample"
    assert first["cursor"] is not None
    cursor = first["cursor"]

    # Wait for at least one newer sample, then sync incrementally.
    for _ in range(40):
        page = (
            await client.get(f"/realtime/streams/{stream_id}/samples?since={cursor}", headers=headers)
        ).json()
        if page["samples"]:
            break
        await asyncio.sleep(0.3)
    assert page["samples"], "no new samples arrived after the cursor"
    assert page["cursor"] > cursor
    seen = {s["ts"] for s in first["samples"]}
    assert all(s["ts"] not in seen for s in page["samples"])  # nothing re-sent

    # A cursor at the head returns an empty page with the cursor unchanged.
    head = page["cursor"]
    empty = (
        await client.get(f"/realtime/streams/{stream_id}/samples?since={head}", headers=headers)
    ).json()
    if not empty["samples"]:
        assert empty["cursor"] == head
```

7. `test_list_streams_attaches_alerts_to_the_right_stream`, tenancy, resume, and delete tests stay unchanged.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd backend && python -m pytest tests/test_realtime.py -v`
Expected: `test_samples_endpoint_returns_only_new_rows_per_cursor` FAILS with 404 (`/samples` route missing); `test_stream_collects_and_computes_metrics` FAILS (snapshot still reads the empty realtime_cache). Others pass.

- [ ] **Step 3: Rewrite backend/app/api/routes/realtime.py**

Replace the file with:

```python
"""Realtime streams API. Collectors (app.services.realtime.StreamCollector)
write one RealtimeSample row per poll window; every read here is a plain DB
query, so any replica can serve any stream. The frontend syncs incrementally
via GET .../samples?since=<cursor> instead of holding an SSE connection."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream
from app.services import alerts as alerts_service
from app.services import realtime as realtime_service

router = APIRouter(prefix="/realtime", tags=["realtime"])


# -----------------------------
# Schemas
# -----------------------------

class StreamCreate(BaseModel):
    integration_id: uuid.UUID
    name: str = ""
    interval_seconds: int = Field(5, ge=1, le=300)
    accumulate: bool = True
    column_map: dict[str, str] = Field(default_factory=dict)
    machine: str | None = None
    sensor_map: dict[str, str] = Field(default_factory=dict)


async def _snapshot_for(db: AsyncSession, org_id: uuid.UUID, stream: RealtimeStream) -> dict:
    snap = await realtime_service.latest_snapshot(db, stream)
    alerts = await alerts_service.stream_alerts(db, org_id, stream.id)
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


def _start(stream: RealtimeStream, integration: Integration) -> None:
    from app.services.integrations import decrypt_config

    realtime_service.start_runner(
        stream_id=stream.id,
        org_id=stream.org_id,
        name=stream.name,
        integration_type=integration.type,
        integration_config=decrypt_config(integration.config),
        column_map=stream.column_map,
        interval_seconds=stream.interval_seconds,
        accumulate=stream.accumulate,
    )


# -----------------------------
# CRUD
# -----------------------------

@router.post("/streams")
async def create_stream(
    req: StreamCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    integration = await get_or_404(db, Integration, current.org_id, req.integration_id, "Integration not found")

    stream = RealtimeStream(
        org_id=current.org_id,
        integration_id=integration.id,
        name=req.name or integration.name,
        column_map=req.column_map,
        machine=req.machine,
        sensor_map=req.sensor_map,
        interval_seconds=req.interval_seconds,
        accumulate=req.accumulate,
        desired_status="running",
        created_by=current.id,
    )
    db.add(stream)
    await log_event(
        db, action="realtime_stream.created", org_id=current.org_id, actor_user_id=current.id,
        metadata={"name": stream.name, "integration_id": str(integration.id)},
    )
    await db.commit()
    await db.refresh(stream)

    _start(stream, integration)
    return await _snapshot_for(db, current.org_id, stream)


@router.get("/streams")
async def list_streams(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(RealtimeStream).where(RealtimeStream.org_id == current.org_id).order_by(RealtimeStream.name))
    ).scalars().all()
    snaps = await realtime_service.latest_snapshots_bulk(db, rows)
    # One query for every stream's alert rules instead of one per stream.
    alerts_by_stream = await alerts_service.stream_alerts_bulk(db, current.org_id, [s.id for s in rows])
    out = []
    for s in rows:
        snap = snaps[str(s.id)]
        snap["alerts"] = [vars(a) for a in alerts_by_stream.get(str(s.id), [])]
        out.append(snap)
    return {"streams": out}


@router.get("/streams/{stream_id}/snapshot")
async def stream_snapshot(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    return await _snapshot_for(db, current.org_id, stream)


@router.get("/streams/{stream_id}/samples")
async def stream_samples(
    stream_id: uuid.UUID,
    since: int | None = None,
    limit: int = 500,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Incremental sync: samples with id > `since`, oldest first (`limit`
    capped at 2000; a truncated page self-heals because `cursor` is the
    highest id returned). Without `since`, returns only the latest sample.
    External metrics (AutoML live scores) are merged into the newest
    returned sample, mirroring the old snapshot-cache merge."""
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    rows, cursor = await realtime_service.samples_since(db, stream_id, since, limit)
    samples = [realtime_service.sample_dict(s) for s in rows]
    if samples:
        external = await realtime_service.external_metrics(db, stream_id)
        if external:
            samples[-1]["metrics"] = {**samples[-1]["metrics"], **external}
    return {"stream_id": str(stream_id), "cursor": cursor, "samples": samples}


@router.get("/streams/{stream_id}/history")
async def stream_history(
    stream_id: uuid.UUID,
    limit: int = 2000,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persisted sample history - durable across restarts and visible from
    every replica (the old per-container JSONL files were neither)."""
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    limit = max(1, min(20_000, int(limit)))
    rows = (
        await db.execute(
            select(RealtimeSample)
            .where(RealtimeSample.stream_id == stream_id)
            .order_by(RealtimeSample.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    points = [
        {"ts": s.ts.isoformat() if s.ts else None, "metrics": s.metrics} for s in reversed(rows)
    ]
    return {"stream_id": str(stream_id), "points": points}


@router.post("/streams/{stream_id}/start")
async def start_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    integration = await get_or_404(db, Integration, current.org_id, stream.integration_id, "Integration not found")

    stream.desired_status = "running"
    stream.last_error = None
    await db.commit()

    _start(stream, integration)
    return await _snapshot_for(db, current.org_id, stream)


@router.post("/streams/{stream_id}/stop")
async def stop_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    await realtime_service.stop_runner(stream.id)
    stream.desired_status = "stopped"
    await db.commit()
    return await _snapshot_for(db, current.org_id, stream)


@router.delete("/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    await realtime_service.stop_runner(stream.id)
    await db.delete(stream)  # realtime_samples rows cascade with the stream
    await log_event(db, action="realtime_stream.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(stream_id))
    await db.commit()
```

Gone: `_snapshot_dict`, `_sse_generator`, `stream_events`, the `StreamingResponse`/`Request`/`asyncio`/`json`/`realtime_cache` imports, and the `delete_history` call.

- [ ] **Step 4: Run the realtime suites**

Run: `cd backend && python -m pytest tests/test_realtime.py tests/test_realtime_sync.py -v`
Expected: PASS (all).

- [ ] **Step 5: Run the wider API suites that touch these routes**

Run: `cd backend && python -m pytest tests/test_realtime.py tests/test_oee.py tests/test_alerts.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/realtime.py backend/tests/test_realtime.py
git commit -m "feat: cursor-based /samples sync replaces the SSE feed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Alerts and AutoML read from the database

**Files:**
- Modify: `backend/app/services/alerts.py` (eval_stream_metric async + bulk)
- Modify: `backend/app/services/automl_realtime.py` (DB-backed rows/metrics)
- Modify: `backend/tests/test_automl_realtime.py` (seed samples instead of cache)

**Interfaces:**
- Consumes: `latest_snapshot`, `latest_snapshots_bulk`, `recent_rows`, `set_external_metric` from Task 4.
- Produces: `async eval_stream_metric(db, org_id, rule) -> AlertTrigger` and pure `_eval_stream_metric_snap(rule, snap) -> AlertTrigger`. `stream_alerts` / `stream_alerts_bulk` keep their signatures (routes already call them with `db, org_id, ...`).

- [ ] **Step 1: Rewrite the failing tests**

In `backend/tests/test_automl_realtime.py`:

1. Replace the imports block: drop `from collections import deque` and `realtime_cache` from the `app.services` import line; add `from app.models.integration import Integration`, `from app.models.realtime import RealtimeSample, RealtimeStream, RealtimeStreamMetric`.
2. Replace `_seed_cache_rows` with:

```python
async def _seed_stream_with_rows(db, rows: list[dict]) -> uuid.UUID:
    """Creates an Integration + (stopped) RealtimeStream + one persisted
    sample carrying `rows` - automl_realtime only ever talks to
    app.services.realtime's recent_rows()/set_external_metric() interface,
    so tests exercise that interface through the real tables."""
    owner = (await db.execute(select(User))).scalars().first()
    integration = Integration(org_id=owner.org_id, name="src", type="erp_rest", config={}, created_by=owner.id)
    db.add(integration)
    await db.flush()
    stream = RealtimeStream(
        org_id=owner.org_id, integration_id=integration.id, name="Live", column_map={},
        sensor_map={}, interval_seconds=5, accumulate=True, desired_status="stopped",
        created_by=owner.id,
    )
    db.add(stream)
    await db.flush()
    if rows:
        db.add(RealtimeSample(
            org_id=owner.org_id, stream_id=stream.id, status="running",
            rows_in_window=len(rows), rows_collected=len(rows), duration_s=1,
            metrics={}, raw_rows=rows,
        ))
    await db.commit()
    return stream.id
```

3. The autouse fixture drops the cache reset:

```python
@pytest.fixture(autouse=True)
def _stop_schedulers_after_each_test():
    yield
    automl_realtime.stop_all_binding_schedulers()
```

4. In `test_score_and_check_binding_publishes_metric_and_fires_stream_alert`: replace `stream_id = uuid.uuid4()` and the `_seed_cache_rows(...)` call by seeding inside the existing session block: `stream_id = await _seed_stream_with_rows(db, [{"x1": i % 2, "x2": (i + 1) % 2, "y": i % 2} for i in range(30)])` as the first statement inside `async with db_session.async_session_factory() as db:`. Replace the snapshot assertions (lines 83-86) with:

```python
    async with db_session.async_session_factory() as db:
        metric = (
            await db.execute(
                select(RealtimeStreamMetric).where(
                    RealtimeStreamMetric.stream_id == stream_id,
                    RealtimeStreamMetric.name == "automl_risk_score",
                )
            )
        ).scalar_one()
        assert metric.value in (0.0, 1.0)  # LogisticRegression.predict -> a class label here
```

and the alert assertion at the end becomes:

```python
    async with db_session.async_session_factory() as db:
        trigger = await alerts.eval_stream_metric(db, org_id, rule)
    assert trigger.triggered is True
```

5. In `test_score_and_check_binding_is_noop_when_no_rows_buffered`: seed with `stream_id = await _seed_stream_with_rows(db, [])` and replace the `realtime_cache.get_snapshot(stream_id) is None` assertion with:

```python
    async with db_session.async_session_factory() as db:
        metric_rows = (
            await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id == stream_id))
        ).scalars().all()
    assert metric_rows == []  # nothing published - no rows to score
```

6. In `test_drift_detected_with_auto_retrain_produces_new_versioned_run`: seed with `stream_id = await _seed_stream_with_rows(db, live_rows)` inside the session block (replacing the `uuid.uuid4()` + `_seed_cache_rows` pair). Everything else stays.

- [ ] **Step 2: Run to verify failures**

Run: `cd backend && python -m pytest tests/test_automl_realtime.py -v`
Expected: FAIL (automl_realtime still reads realtime_cache, which is empty; eval_stream_metric is still sync).

- [ ] **Step 3: Rewrite eval_stream_metric in alerts.py**

Replace the whole `eval_stream_metric` function (lines 299-343) with:

```python
def _eval_stream_metric_snap(rule: AlertRule, snap: dict | None) -> AlertTrigger:
    """Pure half of eval_stream_metric: evaluates one rule against an
    already-fetched snapshot dict (or None when the stream is missing or has
    never produced a sample). Split out so stream_alerts_bulk can prefetch
    snapshots once per stream instead of once per rule."""
    cfg = rule.config
    stream_id = cfg.get("stream_id", "")
    metric_name = cfg.get("metric_name", "")
    comparison = cfg.get("comparison", "gt")  # "gt" | "lt"
    threshold = float(cfg.get("threshold", 0))

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


async def eval_stream_metric(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule) -> AlertTrigger:
    """Targets a live realtime stream instead of a dataset - same
    threshold/offender shape as the dataset evaluators, reading the stream's
    latest persisted RealtimeSample (metrics are per poll window over new
    rows only; thresholds tuned against the old cumulative scale need
    re-tuning)."""
    from app.models.realtime import RealtimeStream
    from app.services import realtime as realtime_service

    stream_id = rule.config.get("stream_id", "")
    stream = None
    if stream_id:
        stream = (
            await db.execute(
                select(RealtimeStream).where(
                    RealtimeStream.id == uuid.UUID(stream_id), RealtimeStream.org_id == org_id
                )
            )
        ).scalar_one_or_none()
    if stream is None:
        return _eval_stream_metric_snap(rule, None)
    snap = await realtime_service.latest_snapshot(db, stream)
    if snap.get("ts") is None and not snap.get("metrics"):
        return _eval_stream_metric_snap(rule, None)  # no sample and no external metric yet
    return _eval_stream_metric_snap(rule, snap)
```

The em dash inside "Stream not running — nothing to evaluate." is preexisting app copy carried verbatim.

Then update the dispatcher at line 578: `return eval_stream_metric(rule)` becomes `return await eval_stream_metric(db, org_id, rule)`.

Update `stream_alerts` (line ~673): the final line `return [eval_stream_metric(r) for r in result.scalars().all()]` becomes:

```python
    rules = result.scalars().all()
    if not rules:
        return []
    from app.models.realtime import RealtimeStream
    from app.services import realtime as realtime_service

    stream = (
        await db.execute(
            select(RealtimeStream).where(RealtimeStream.id == stream_id, RealtimeStream.org_id == org_id)
        )
    ).scalar_one_or_none()
    snap = await realtime_service.latest_snapshot(db, stream) if stream is not None else None
    if snap is not None and snap.get("ts") is None and not snap.get("metrics"):
        snap = None
    return [_eval_stream_metric_snap(r, snap) for r in rules]
```

Update `stream_alerts_bulk`: the loop `by_stream.setdefault(...).append(eval_stream_metric(rule))` becomes:

```python
    rules = result.scalars().all()
    if not rules:
        return {}
    from app.models.realtime import RealtimeStream
    from app.services import realtime as realtime_service

    rule_sids = {rule.config.get("stream_id") for rule in rules if rule.config.get("stream_id")}
    streams = (
        await db.execute(
            select(RealtimeStream).where(
                RealtimeStream.org_id == org_id,
                RealtimeStream.id.in_([uuid.UUID(s) for s in rule_sids]),
            )
        )
    ).scalars().all()
    snaps = await realtime_service.latest_snapshots_bulk(db, streams)
    by_stream: dict[str, list[AlertTrigger]] = {}
    for rule in rules:
        sid = rule.config.get("stream_id")
        snap = snaps.get(sid)
        if snap is not None and snap.get("ts") is None and not snap.get("metrics"):
            snap = None
        by_stream.setdefault(sid, []).append(_eval_stream_metric_snap(rule, snap))
    return by_stream
```

- [ ] **Step 4: Rewire automl_realtime.py**

In `backend/app/services/automl_realtime.py`:
- Import swap: `from app.services import realtime_cache` becomes `from app.services import realtime as realtime_service`.
- Line 101: `rows = realtime_cache.recent_rows(binding.stream_id, _WINDOW_ROWS)` becomes `rows = await realtime_service.recent_rows(db, binding.stream_id, _WINDOW_ROWS)`.
- Line 115: `realtime_cache.set_external_metric(binding.stream_id, binding.metric_name, score)` becomes `await realtime_service.set_external_metric(db, binding.stream_id, binding.metric_name, score)` (the existing `await db.commit()` at the end of `_tick` persists it).
- Line 167: `rows = realtime_cache.recent_rows(binding.stream_id, 10_000)` becomes `rows = await realtime_service.recent_rows(db, binding.stream_id, 10_000)`.
- Module docstring: replace step 1's parenthetical "(app.services.realtime_cache. recent_rows(), sourced from the realtime.ticks Redpanda topic - not the Flink job's windowed metrics, ...)" with "(app.services.realtime.recent_rows(), flattened from the stream's persisted RealtimeSample raw_rows - not the windowed metrics, which don't carry the raw feature columns a trained pipeline needs)", replace "publishes the result via realtime_cache.set_external_metric()" with "publishes the result via app.services.realtime.set_external_metric()", and replace the final paragraph ("Reads via realtime_cache rather than ... same posture as before.") with "Reads and publishes via Postgres, so scoring/drift work from any replica with no warm-up: a fresh replica sees the stream's full persisted sample history immediately."

- [ ] **Step 5: Run the affected suites**

Run: `cd backend && python -m pytest tests/test_automl_realtime.py tests/test_realtime.py tests/test_alerts.py tests/test_alerts_webhook.py tests/test_automl_risk_alert.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/alerts.py backend/app/services/automl_realtime.py backend/tests/test_automl_realtime.py
git commit -m "feat: stream alerts and AutoML live scoring read Postgres samples

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Delete the broker stack

**Files:**
- Delete: `backend/app/services/realtime_cache.py`, `backend/app/services/sensor_consumer.py`, `backend/streaming/` (entire directory), `backend/tests/test_realtime_cache.py`, `backend/tests/test_realtime_cache_external.py`, `backend/tests/test_realtime_producer.py`
- Modify: `backend/app/services/realtime.py` (drop history shims), `backend/app/main.py`, `backend/dev_server.py`, `backend/app/core/config.py`, `backend/requirements.txt`, `docker-compose.yml`, `backend/app/models/sensor.py:1-4` (docstring), `backend/app/models/realtime.py` (docstring line re sensor_consumer)

**Interfaces:**
- Consumes: nothing new. This task only removes code whose last importer died in Tasks 5 and 6.
- Produces: a tree with zero kafka/redpanda/flink references outside docs.

- [ ] **Step 1: Delete the files**

```bash
cd backend
git rm app/services/realtime_cache.py app/services/sensor_consumer.py
git rm -r streaming
git rm tests/test_realtime_cache.py tests/test_realtime_cache_external.py tests/test_realtime_producer.py
```

- [ ] **Step 2: Remove the wiring**

`backend/app/main.py`: delete the imports `from app.services import realtime_cache` and `from app.services import sensor_consumer`; in `lifespan` delete `await realtime_cache.start_cache_consumer()` (and its two comment lines), `await sensor_consumer.start_sensor_consumer()`, `await sensor_consumer.stop_sensor_consumer()`, `await realtime_cache.stop_cache_consumer()`.

`backend/dev_server.py`: delete `from app.services import realtime_cache` and the four patch lines plus their comment block (lines 106-113: the `_noop` def stays only if still used; after removing `realtime_service.resume_all_streams = _noop`, `realtime_service.stop_all_runners = _noop`, `realtime_cache.start_cache_consumer = _noop`, `realtime_cache.stop_cache_consumer = _noop`, delete `_noop` too). Collectors run fine against the dev SQLite file now; no fake broker is needed, which was the patches' whole purpose. Also delete the now-unused `from app.services import realtime as realtime_service` import if nothing else in the file uses it.

`backend/app/services/realtime.py`: delete the entire "TEMPORARY JSONL history shims" section (`_history_dir`, `history_path`, `read_history`, `delete_history`) and the now-unused `from pathlib import Path` import.

`backend/app/core/config.py`: delete the `redpanda_brokers` line and its three comment lines (63-67).

`backend/requirements.txt`: delete lines 69-71 (the "Realtime streaming: Redpanda..." comment, `kafka-python==3.0.8`, `aiokafka==0.11.0`).

`docker-compose.yml`: delete the `redpanda`, `flink-jobmanager`, and `flink-taskmanager` services; in the `backend` service delete the `REDPANDA_BROKERS: redpanda:9092` env line and the `redpanda: condition: service_healthy` depends_on entry.

Docstring sweeps: `backend/app/models/sensor.py` first line becomes `"""CBM sensor telemetry - written by app.services.sensor_ingest as the realtime collector polls streams with a sensor_map configured, scored by` (rest unchanged). `backend/app/models/realtime.py` line 46 comment `# See app.services.sensor_consumer.` becomes `# See app.services.sensor_ingest.`.

- [ ] **Step 3: Verify nothing still references the dead stack**

Run: `cd backend && grep -rn "realtime_cache\|sensor_consumer\|streaming\.\|kafka\|redpanda\|flink" app tests dev_server.py --include="*.py" -i`
Expected: no output. Then: `grep -rni "redpanda\|flink" ../docker-compose.yml` expected: no output.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && python -m pytest tests -x -q`
Expected: PASS (all tests, no collection errors)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat!: remove Redpanda/Flink streaming stack

Realtime now streams into Postgres and syncs by cursor; no broker to
provision, so realtime metrics work in deployments without one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Frontend cursor sync

**Files:**
- Delete: `frontend/src/lib/sse.ts`
- Modify: `frontend/src/pages/Realtime.tsx`

**Interfaces:**
- Consumes: `GET /realtime/streams/{id}/samples?since=<cursor>` from Task 5 (`{stream_id, cursor, samples}`), snapshot fields `interval_seconds` and status `starting`.
- Produces: `LiveMetrics` polling incrementally; no SSE anywhere in the frontend.

- [ ] **Step 1: Delete sse.ts and rewrite LiveMetrics**

```bash
cd frontend && git rm src/lib/sse.ts
```

In `frontend/src/pages/Realtime.tsx`:

1. Replace the import `import { subscribeToSSE } from "../lib/sse";` with `import { useEffect, useRef, useState } from "react";` merged into the existing react import (line 1 becomes `import { useEffect, useRef, useState } from "react";`) and delete the sse import line.
2. Update the `StreamSnapshot` interface: the `status` union becomes `"running" | "error" | "stopped" | "starting"`, and add `interval_seconds?: number;` after `metrics`.
3. Update `STATUS_VARIANT`: remove `not_running_in_this_process: "warning",` and add `starting: "warning",`.
4. Add below the interface:

```tsx
interface SamplesResponse {
  stream_id: string;
  cursor: number | null;
  samples: Partial<StreamSnapshot>[];
}
```

5. Replace the whole `LiveMetrics` component's effect (lines 66-75) with:

```tsx
function LiveMetrics({ streamId, initial }: { streamId: string; initial: StreamSnapshot }) {
  const [snap, setSnap] = useState<StreamSnapshot>(initial);
  const cursor = useRef<number | null>(null);

  // Incremental sync: pass back the last cursor so the server returns only
  // samples written since the previous poll - nothing is ever re-sent.
  useEffect(() => {
    let cancelled = false;
    const sync = async () => {
      try {
        const qs = cursor.current != null ? `?since=${cursor.current}` : "";
        const res = await apiFetch(`/realtime/streams/${streamId}/samples${qs}`);
        if (!res.ok || cancelled) return;
        const data: SamplesResponse = await res.json();
        if (cancelled) return;
        if (data.cursor != null) cursor.current = data.cursor;
        const latest = data.samples[data.samples.length - 1];
        if (latest) setSnap((prev) => ({ ...prev, ...latest }));
      } catch {
        // transient network error - next interval retries
      }
    };
    sync();
    const id = setInterval(sync, (initial.interval_seconds ?? 5) * 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [streamId]);
```

(the JSX below the effect is unchanged).

6. Update the stale comment on the list query's `refetchInterval: 5000` line (line 131): `// list view uses polling; the live detail view below uses SSE` becomes `// list view polls; the detail dialog syncs incrementally by cursor`.

- [ ] **Step 2: Type-check, lint, test, build**

Run: `cd frontend && npm run build && npm run test`
Expected: `tsc -b` clean (would fail if any other file still imported sse.ts), vitest suite PASS.

- [ ] **Step 3: Commit**

```bash
git add -A frontend/src
git commit -m "feat: Realtime detail view syncs samples by cursor instead of SSE

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Final verification

**Files:** none new.

- [ ] **Step 1: Full backend suite**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS, zero failures, zero collection errors.

- [ ] **Step 2: Frontend build + tests**

Run: `cd frontend && npm run build && npm run test && npm run lint`
Expected: all clean.

- [ ] **Step 3: Residue sweep**

Run: `grep -rniE "redpanda|flink|kafka|realtime_cache|\bsse\b|EventSource|event-stream" backend/app backend/tests frontend/src docker-compose.yml`
Expected: no hits (the `\bsse\b` word boundary avoids false positives like "assess"; docs/ and specs are exempt and not in the search path).

- [ ] **Step 4: Update the knowledge graph**

Run: `graphify update .` (project rule after code changes; AST-only, no API cost).

- [ ] **Step 5: Commit any stragglers**

```bash
git status --short
```

If clean, done. Otherwise stage and commit with an appropriate message.
