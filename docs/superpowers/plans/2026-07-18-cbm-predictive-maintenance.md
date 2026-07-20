# CBM Predictive Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OEE/CSV-based `Predictive Maintenance` feature with real condition-based
monitoring (CBM): live per-machine sensor ingestion over the existing Redpanda pipeline,
SPC anomaly detection reusing the app's existing Nelson-rule control-chart engine, and
Weibull survival-analysis RUL calibrated against real breakdown history.

**Architecture:** `TickProducer`/`realtime.ticks` (existing, unchanged) → new `SensorConsumer`
(Python Kafka consumer, mirrors `CacheConsumer`) extracts named sensor fields into a new
`SensorReading` Postgres table, scoring each write with the existing `spc_calc.individuals_chart`
Nelson-rule engine → `SensorBaseline`/`SensorAnomaly` persisted. RUL comes from a Weibull
survival fit over `DowntimeIncident` breakdown history, lazily refit on first read per day
(mirrors `morning_brief.py`'s existing no-scheduler pattern). Both feed two new `AlertRule`
types and a rewritten `Predictive.tsx` (fleet dashboard + per-machine detail).

**Tech Stack:** FastAPI, SQLAlchemy (async, Postgres), Alembic, aiokafka, `lifelines` (new
dependency, Weibull MLE), React + TanStack Query, ECharts (existing `EChart`/`controlChart`).

## Global Constraints

- Every new SQLAlchemy model follows the existing pattern exactly: `Base` (no mixins),
  `id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)`,
  `org_id` as `ForeignKey("orgs.id", ondelete="CASCADE")` + `index=True`, timestamps via
  `mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))`.
- Every new model must be added to the import block in both `backend/tests/conftest.py`
  (`from app.models import (...)`, alphabetical) and `backend/alembic/env.py` (same list,
  alphabetical) or its table silently won't exist in tests / autogenerate.
- No new job scheduler, no new asyncio background loop beyond the one Kafka consumer this
  plan adds. Anything that needs periodic recompute uses the existing lazy
  recompute-on-first-read-per-day pattern (`backend/app/services/morning_brief.py`'s
  `_ensure_todays_snapshot`), not a new mechanism.
- Anomaly detection reuses `backend/app/analytics/spc_calc.py`'s `individuals_chart`/
  `nelson_violations` as-is. Do not write a new statistical scorer.
- Current alembic head is `f1a5c8d3e942`. All new migrations chain from there (or from
  each other, in task order).
- Run backend tests with `cd backend && python -m pytest tests/ -v` (or narrower with
  `-k`/a specific file). Run frontend type-check with `cd frontend && npm run build` (no
  component-render test framework exists in this repo — only `vitest run` for pure-logic
  unit tests, so new UI is verified via a Vitest logic test where there's pure logic to
  test, and manual/build-level verification otherwise, consistent with the rest of the
  codebase).

---

## Task 1: `RealtimeStream` gains `machine` + `sensor_map`

**Files:**
- Modify: `backend/app/models/realtime.py`
- Modify: `backend/app/api/routes/realtime.py`
- Create: `backend/alembic/versions/a9c3e7f15d82_realtime_stream_sensor_fields.py`
- Test: `backend/tests/test_realtime.py` (add one test; file already exists per earlier
  git history — if it doesn't exist, create it following the `client` fixture pattern
  used throughout `backend/tests/test_predictive.py`)

**Interfaces:**
- Produces: `RealtimeStream.machine: str | None`, `RealtimeStream.sensor_map: dict[str, str]`
  — consumed by Task 4's `SensorConsumer`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_realtime.py` (create the file with the header below if it
doesn't already exist — check first with a file read):

```python
from __future__ import annotations

import pytest

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


async def test_create_stream_with_machine_and_sensor_map(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations",
        headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201, integ.text
    integration_id = integ.json()["id"]

    res = await client.post(
        "/realtime/streams",
        headers=_h(token),
        json={
            "integration_id": integration_id,
            "name": "Line 1 sensors",
            "machine": "M1",
            "sensor_map": {"vib_x": "vibration_x_mm_s", "brg_temp": "bearing_temp_c"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stream_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_realtime.py::test_create_stream_with_machine_and_sensor_map -v`
Expected: FAIL — `422 Unprocessable Entity` (unknown fields `machine`/`sensor_map`) or a
`TypeError` on `RealtimeStream(...)`, since the model/schema don't have these fields yet.

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/realtime.py`, after the `column_map` line (currently line 40), add:

```python
    # Which machine this stream's ticks belong to - a stream carries one row
    # per poll (one machine's worth of data), never multiple machines mixed
    # together. None = this stream stays OEE-only, unaffected by CBM.
    machine: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {"vib_x": "vibration_x_mm_s", ...} - sensor channel name -> the field
    # name to read from Tick.row. See app.services.sensor_consumer.
    sensor_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
```

- [ ] **Step 4: Generate the migration**

Create `backend/alembic/versions/a9c3e7f15d82_realtime_stream_sensor_fields.py`:

```python
"""realtime stream sensor fields

Revision ID: a9c3e7f15d82
Revises: f1a5c8d3e942
Create Date: 2026-07-18 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a9c3e7f15d82"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("realtime_streams", sa.Column("machine", sa.String(length=255), nullable=True))
    op.add_column(
        "realtime_streams",
        sa.Column("sensor_map", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("realtime_streams", "sensor_map")
    op.drop_column("realtime_streams", "machine")
```

- [ ] **Step 5: Update the API schema and create-stream handler**

In `backend/app/api/routes/realtime.py`, modify `StreamCreate` (currently lines 40-45):

```python
class StreamCreate(BaseModel):
    integration_id: uuid.UUID
    name: str = ""
    interval_seconds: int = Field(5, ge=1, le=300)
    accumulate: bool = True
    column_map: dict[str, str] = Field(default_factory=dict)
    machine: str | None = None
    sensor_map: dict[str, str] = Field(default_factory=dict)
```

In `create_stream` (currently lines 105-130), add the two fields to the `RealtimeStream(...)`
construction:

```python
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
```

- [ ] **Step 6: Run the migration and the test**

Run: `cd backend && python -m alembic upgrade head`
Expected: applies `a9c3e7f15d82` with no errors.

Run: `cd backend && python -m pytest tests/test_realtime.py -v`
Expected: PASS (all tests in the file, including the new one).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/realtime.py backend/app/api/routes/realtime.py backend/alembic/versions/a9c3e7f15d82_realtime_stream_sensor_fields.py backend/tests/test_realtime.py
git commit -m "feat: add machine + sensor_map to RealtimeStream for CBM ingestion"
```

---

## Task 2: New sensor models — `SensorReading`, `SensorBaseline`, `SensorAnomaly`, `WeibullFit`

**Files:**
- Create: `backend/app/models/sensor.py`
- Create: `backend/alembic/versions/b4d8f2a96e37_sensor_cbm_tables.py`
- Modify: `backend/tests/conftest.py` (register `sensor` in the model import block)
- Modify: `backend/alembic/env.py` (register `sensor` in the model import block)

**Interfaces:**
- Produces: `SensorReading(id, org_id, machine, sensor, value, ts)`,
  `SensorBaseline(id, org_id, machine, sensor, n, centerline, ucl, lcl, mr_bar, updated_at)`,
  `SensorAnomaly(id, org_id, machine, sensor, reading_id, violations, detected_at, acknowledged_at)`,
  `WeibullFit(id, org_id, machine, shape_k, scale_lambda, n_failures, n_censored, fit_tier, fit_at)`
  — consumed by Tasks 3-7.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sensor_models.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading, WeibullFit

pytestmark = pytest.mark.asyncio


async def test_sensor_models_round_trip(db_session):
    org_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=4.2, ts=now)
    db_session.add(reading)
    await db_session.flush()

    baseline = SensorBaseline(
        org_id=org_id, machine="M1", sensor="vib_x", n=25,
        centerline=4.0, ucl=5.0, lcl=3.0, mr_bar=0.3,
    )
    db_session.add(baseline)

    anomaly = SensorAnomaly(
        org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1],
    )
    db_session.add(anomaly)

    fit = WeibullFit(
        org_id=org_id, machine="M1", shape_k=1.8, scale_lambda=45.0,
        n_failures=6, n_censored=1, fit_tier="per_machine",
    )
    db_session.add(fit)
    await db_session.commit()

    got_reading = (await db_session.execute(select(SensorReading).where(SensorReading.org_id == org_id))).scalar_one()
    assert got_reading.sensor == "vib_x"
    got_anomaly = (await db_session.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == org_id))).scalar_one()
    assert got_anomaly.violations == [1]
    got_fit = (await db_session.execute(select(WeibullFit).where(WeibullFit.org_id == org_id))).scalar_one()
    assert got_fit.fit_tier == "per_machine"
```

This test needs a `db_session` fixture. Check `backend/tests/conftest.py` first — if only
a `client` fixture exists (no bare `db_session` fixture), add one following the same
`async_session_factory`/`test_engine` setup already used by the `client` fixture, e.g.:

```python
@pytest.fixture
async def db_session(test_engine):
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
```

(Match whatever the existing `client` fixture's session construction actually looks like
— read `backend/tests/conftest.py` in full before adding this, since the exact fixture
dependency chain (`test_engine` name, table-creation fixture) must be reused, not
duplicated.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sensor_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.sensor'`

- [ ] **Step 3: Create the models**

Create `backend/app/models/sensor.py`:

```python
"""CBM sensor telemetry - written by app.services.sensor_consumer as it drains
realtime.ticks for streams with a sensor_map configured, scored by
app.services.anomaly (which reuses app.analytics.spc_calc's existing
Individuals/Moving-Range control chart), and read by app.services.cbm."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SensorBaseline(Base):
    """Cached I-MR control limits for one (machine, sensor), refreshed on every
    SensorReading write from spc_calc.individuals_chart's output so reads
    don't recompute over the full window per request."""

    __tablename__ = "sensor_baselines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    centerline: Mapped[float | None] = mapped_column(Float, nullable=True)
    ucl: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcl: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SensorAnomaly(Base):
    """One out-of-control reading. violations holds the Nelson rule numbers
    (1-8) from spc_calc.nelson_violations, e.g. [1] or [1, 6]."""

    __tablename__ = "sensor_anomalies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    reading_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sensor_readings.id", ondelete="CASCADE"), nullable=False
    )
    violations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeibullFit(Base):
    """Latest Weibull survival fit for one machine. fit_tier records which
    fallback tier produced it (per_machine / fleet_pooled / insufficient_data)
    so callers know how much to trust the number. Refreshed lazily on first
    read each day by app.services.rul.get_or_refit - see that module's
    docstring for why there's no scheduled job."""

    __tablename__ = "weibull_fits"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    shape_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_lambda: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_censored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fit_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    fit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Create the migration**

Create `backend/alembic/versions/b4d8f2a96e37_sensor_cbm_tables.py`:

```python
"""sensor cbm tables

Revision ID: b4d8f2a96e37
Revises: a9c3e7f15d82
Create Date: 2026-07-18 00:00:01.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b4d8f2a96e37"  # pragma: allowlist secret
down_revision = "a9c3e7f15d82"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_readings_org_machine_sensor_ts", "sensor_readings",
        ["org_id", "machine", "sensor", "ts"],
    )

    op.create_table(
        "sensor_baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("centerline", sa.Float(), nullable=True),
        sa.Column("ucl", sa.Float(), nullable=True),
        sa.Column("lcl", sa.Float(), nullable=True),
        sa.Column("mr_bar", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_baselines_org_machine_sensor", "sensor_baselines",
        ["org_id", "machine", "sensor"], unique=True,
    )

    op.create_table(
        "sensor_anomalies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("reading_id", sa.Uuid(), nullable=False),
        sa.Column("violations", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reading_id"], ["sensor_readings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_anomalies_org_machine_detected", "sensor_anomalies",
        ["org_id", "machine", "detected_at"],
    )

    op.create_table(
        "weibull_fits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("shape_k", sa.Float(), nullable=True),
        sa.Column("scale_lambda", sa.Float(), nullable=True),
        sa.Column("n_failures", sa.Integer(), nullable=False),
        sa.Column("n_censored", sa.Integer(), nullable=False),
        sa.Column("fit_tier", sa.String(length=32), nullable=False),
        sa.Column("fit_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_weibull_fits_org_machine", "weibull_fits", ["org_id", "machine"], unique=True,
    )


def downgrade() -> None:
    op.drop_table("weibull_fits")
    op.drop_table("sensor_anomalies")
    op.drop_table("sensor_baselines")
    op.drop_table("sensor_readings")
```

- [ ] **Step 5: Register the model in both import blocks**

In `backend/tests/conftest.py`, add `sensor,` to the `from app.models import (...)` block,
alphabetically between `scenario,` and `shift_schedule,`.

In `backend/alembic/env.py`, add `sensor,` to the `from app.models import (...)` block,
alphabetically between `scenario,` and `shift_schedule,`.

- [ ] **Step 6: Run the migration and the test**

Run: `cd backend && python -m alembic upgrade head`
Expected: applies `b4d8f2a96e37` with no errors.

Run: `cd backend && python -m pytest tests/test_sensor_models.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/sensor.py backend/alembic/versions/b4d8f2a96e37_sensor_cbm_tables.py backend/tests/conftest.py backend/alembic/env.py backend/tests/test_sensor_models.py
git commit -m "feat: add SensorReading/SensorBaseline/SensorAnomaly/WeibullFit models"
```

---

## Task 3: Anomaly scoring — reuse `spc_calc`

**Files:**
- Create: `backend/app/services/anomaly.py`
- Test: `backend/tests/test_anomaly.py`

**Interfaces:**
- Consumes: `app.analytics.spc_calc.individuals_chart(values, labels)`,
  `app.analytics.spc_calc.MIN_BASELINE` (existing, unchanged).
- Produces: `score_reading(values: list[float], timestamps: list[datetime]) -> AnomalyResult`
  where `AnomalyResult` has `in_control: bool`, `violations: list[int]`, `centerline: float`,
  `ucl: float`, `lcl: float`, `mr_bar: float`, `n: int`, `learning: bool` — consumed by
  Task 4's `SensorConsumer`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_anomaly.py`:

```python
from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.services.anomaly import score_reading


def test_score_reading_learning_period_before_min_baseline():
    base = datetime(2026, 1, 1)
    values = [10.0] * 5
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is True
    assert result.in_control is True
    assert result.violations == []


def test_score_reading_flags_out_of_control_spike():
    base = datetime(2026, 1, 1)
    values = [10.0] * 25 + [50.0]  # stable baseline, then a huge spike
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is False
    assert result.in_control is False
    assert 1 in result.violations  # Nelson rule 1: point beyond 3-sigma


def test_score_reading_in_control_for_stable_series():
    random.seed(42)
    base = datetime(2026, 1, 1)
    values = [round(10.0 + random.gauss(0, 0.15), 3) for _ in range(25)]
    timestamps = [base + timedelta(hours=i) for i in range(len(values))]

    result = score_reading(values, timestamps)

    assert result.learning is False
    assert result.in_control is True
    assert result.violations == []
```

(The third test's fixed seed/values were verified directly against
`spc_calc.individuals_chart` before writing this plan — `out_of_control_count == 0` for
this exact series. Do not change the seed or generation without re-verifying, since the
Nelson rules are pattern-sensitive and a different random draw could legitimately trigger
one.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_anomaly.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.anomaly'`

- [ ] **Step 3: Implement**

Create `backend/app/services/anomaly.py`:

```python
"""Anomaly scoring for CBM sensor readings - wraps app.analytics.spc_calc's
existing Individuals/Moving-Range control chart (all 8 Nelson rules) instead
of a new bespoke statistic, so CBM gets the same rigor already shipped on the
SPC analytics page rather than a second, weaker implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.analytics.spc_calc import MIN_BASELINE, individuals_chart

WINDOW = 200  # trailing readings considered per (machine, sensor) score


@dataclass
class AnomalyResult:
    in_control: bool
    violations: list[int]
    centerline: float
    ucl: float
    lcl: float
    mr_bar: float
    n: int
    learning: bool  # True while n < MIN_BASELINE - no violation is meaningful yet


def score_reading(values: list[float], timestamps: list[datetime]) -> AnomalyResult:
    """`values`/`timestamps` are the trailing window for one (machine, sensor),
    oldest first, with the newest reading last. All control-limit math is
    delegated to spc_calc.individuals_chart; this just reads off the last
    point and repackages it."""
    n = len(values)
    if n < MIN_BASELINE:
        return AnomalyResult(True, [], 0.0, 0.0, 0.0, 0.0, n, learning=True)

    labels = [ts.isoformat() for ts in timestamps]
    chart = individuals_chart(values, labels)
    last = chart["points"][-1]
    return AnomalyResult(
        in_control=last["in_control"],
        violations=last["violations"],
        centerline=chart["centerline"],
        ucl=chart["ucl"],
        lcl=chart["lcl"],
        mr_bar=chart["range_centerline"],
        n=n,
        learning=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_anomaly.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/anomaly.py backend/tests/test_anomaly.py
git commit -m "feat: add CBM anomaly scoring, reusing the existing SPC/Nelson-rule engine"
```

---

## Task 4: `SensorConsumer` — Kafka ingestion into `SensorReading`

**Files:**
- Create: `backend/app/services/sensor_consumer.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sensor_consumer.py`

**Interfaces:**
- Consumes: `streaming.schemas.Tick`/`TICKS_TOPIC` (existing), `RealtimeStream.machine`/
  `sensor_map` (Task 1), `app.services.anomaly.score_reading` (Task 3),
  `SensorReading`/`SensorBaseline`/`SensorAnomaly` (Task 2).
- Produces: `SensorConsumer` class with `start()`/`stop()`/`_handle_tick(tick)`,
  module functions `start_sensor_consumer()`/`stop_sensor_consumer()` — consumed by
  Task 4 Step 5 (`main.py` lifespan wiring).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sensor_consumer.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorReading
from app.services.sensor_consumer import SensorConsumer
from streaming.schemas import Tick

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


async def test_handle_tick_writes_sensor_reading_and_flags_anomaly(client):
    token = await _register_and_login(client)
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
    stream_id = stream_res.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()

    consumer = SensorConsumer()

    # 24 stable readings to build the baseline, then one spike - mirrors
    # test_anomaly.py's out-of-control-spike case.
    for i in range(24):
        tick = Tick(
            stream_id=stream_id, org_id=str(org_id), column_map={}, row={"vibration_x_mm_s": 10.0},
            status="running", error=None, collected_at=datetime.now(timezone.utc).isoformat(),
        )
        await consumer._handle_tick(tick)

    spike_tick = Tick(
        stream_id=stream_id, org_id=str(org_id), column_map={}, row={"vibration_x_mm_s": 50.0},
        status="running", error=None, collected_at=datetime.now(timezone.utc).isoformat(),
    )
    await consumer._handle_tick(spike_tick)

    async with db_session.async_session_factory() as db:
        readings = (
            await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))
        ).scalars().all()
        assert len(readings) == 25

        anomalies = (
            await db.execute(select(SensorAnomaly).where(SensorAnomaly.machine == "M1"))
        ).scalars().all()
        assert len(anomalies) == 1
        assert 1 in anomalies[0].violations
```

Note: `MIN_BASELINE` in `spc_calc.py` is 20, so 24 readings is enough to leave the
learning period before the 25th (spike) reading is scored.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sensor_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.sensor_consumer'`

- [ ] **Step 3: Implement**

Create `backend/app/services/sensor_consumer.py`:

```python
"""Asyncio background consumer of the realtime.ticks Redpanda topic - drains
ticks for streams that have a sensor_map configured, extracts named sensor
channels, persists them as SensorReading rows, and scores each new reading
via app.services.anomaly. Mirrors app.services.realtime_cache.CacheConsumer's
structure (own consumer group per replica, asyncio background task) but
writes to Postgres instead of an in-memory snapshot cache.

ponytail: no in-memory RealtimeStream cache - looks the stream up per tick.
At typical 5s+ poll intervals this is a bounded, cheap query; add caching
only if this consumer is shown to be DB-bound."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.anomaly import WINDOW, score_reading
from streaming.schemas import TICKS_TOPIC, Tick

logger = logging.getLogger(__name__)


class SensorConsumer:
    def __init__(self, group_id: str | None = None, brokers: str | None = None):
        self.group_id = group_id or f"sensor-consumer-{socket.gethostname()}-{os.getpid()}"
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
            if stream is None or not stream.sensor_map or not stream.machine:
                return

            now = datetime.now(timezone.utc)
            for sensor, field in stream.sensor_map.items():
                raw = tick.row.get(field)
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
    _instance = SensorConsumer(brokers=brokers)
    await _instance.start()


async def stop_sensor_consumer() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
        _instance = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sensor_consumer.py -v`
Expected: PASS

- [ ] **Step 5: Wire into `main.py` lifespan**

In `backend/app/main.py`, find the `lifespan` function (around lines 58-68). Add the
import at the top of the file alongside the existing `realtime_cache` import, and start/stop
`sensor_consumer` alongside `realtime_cache`:

```python
from app.services import sensor_consumer
```

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await realtime_service.resume_all_streams()
    await realtime_cache.start_cache_consumer()
    await sensor_consumer.start_sensor_consumer()
    yield
    await sensor_consumer.stop_sensor_consumer()
    await realtime_cache.stop_cache_consumer()
    realtime_service.stop_all_runners()
```

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS (no regressions from the lifespan change — the test `client` fixture
overrides DB/Redis but the app still goes through `lifespan`, so this confirms
`start_sensor_consumer`/`stop_sensor_consumer` don't break app startup in tests, e.g. if
Kafka isn't reachable in the test environment. If this fails because `AIOKafkaConsumer.start()`
raises when no broker is reachable, wrap the call the same defensive way the rest of this
codebase treats optional infrastructure — check how `realtime_cache.start_cache_consumer()`
already behaves in the test suite today (it's called the same way in the same lifespan and
evidently doesn't fail tests), and mirror that exact behavior for `sensor_consumer` rather
than inventing new error handling.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/sensor_consumer.py backend/app/main.py backend/tests/test_sensor_consumer.py
git commit -m "feat: add SensorConsumer, draining realtime.ticks into SensorReading"
```

---

## Task 5: RUL — Weibull survival fitting

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/services/rul.py`
- Test: `backend/tests/test_rul.py`

**Interfaces:**
- Consumes: `DowntimeIncident` (existing model, `backend/app/models/downtime_tracking.py`),
  `WeibullFit` (Task 2).
- Produces: `async def get_or_refit(db: AsyncSession, org_id: uuid.UUID, machine: str) -> WeibullFit`,
  `def failure_probability_30d(fit: WeibullFit) -> float | None` — consumed by Task 6
  (alerting) and Task 7 (CBM API).

- [ ] **Step 1: Add the `lifelines` dependency**

In `backend/requirements.txt`, add after the `scipy>=1.11.0` line:

```
lifelines>=0.28.0  # Weibull survival analysis for CBM RUL (backend/app/services/rul.py)
```

Run: `cd backend && pip install -r requirements.txt`
Expected: installs `lifelines` and its dependencies with no errors.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_rul.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.downtime_tracking import DowntimeIncident
from app.services.rul import failure_probability_30d, get_or_refit

pytestmark = pytest.mark.asyncio


async def _make_breakdown(db_session, org_id, user_id, machine, started_at, ended_at):
    incident = DowntimeIncident(
        org_id=org_id, started_by=user_id, machine=machine, reason="breakdown",
        shift="Day", started_at=started_at, ended_at=ended_at,
    )
    db_session.add(incident)


async def test_get_or_refit_per_machine_tier_with_enough_failures(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 6 breakdowns roughly every 20 days - enough for the per_machine tier (>=5).
    for i in range(6):
        started = now - timedelta(days=(6 - i) * 20)
        await _make_breakdown(db_session, org_id, user_id, "M1", started, started + timedelta(hours=2))
    await db_session.commit()

    fit = await get_or_refit(db_session, org_id, "M1")

    # 6 breakdown events produce 5 *completed* inter-breakdown intervals (the
    # gaps between consecutive breakdowns) plus one still-open, right-censored
    # interval since the last one - n_failures counts completed intervals
    # (the uncensored samples the Weibull fit actually uses), not raw events.
    assert fit.fit_tier == "per_machine"
    assert fit.n_failures == 5
    assert fit.n_censored == 1
    assert fit.shape_k is not None
    assert fit.scale_lambda is not None
    prob = failure_probability_30d(fit)
    assert prob is not None
    assert 0.0 <= prob <= 1.0


async def test_get_or_refit_insufficient_data_tier(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Only 1 breakdown fleet-wide - below both tiers' minimum of 5.
    await _make_breakdown(db_session, org_id, user_id, "M2", now - timedelta(days=10), now - timedelta(days=9))
    await db_session.commit()

    fit = await get_or_refit(db_session, org_id, "M2")

    assert fit.fit_tier == "insufficient_data"
    assert fit.shape_k is None
    assert failure_probability_30d(fit) is None


async def test_get_or_refit_is_lazy_per_day(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    for i in range(6):
        started = now - timedelta(days=(6 - i) * 20)
        await _make_breakdown(db_session, org_id, user_id, "M3", started, started + timedelta(hours=2))
    await db_session.commit()

    first = await get_or_refit(db_session, org_id, "M3")
    first_fit_at = first.fit_at

    # A second call the same day must return the same persisted row, not refit.
    second = await get_or_refit(db_session, org_id, "M3")
    assert second.id == first.id
    assert second.fit_at == first_fit_at
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rul.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rul'`

- [ ] **Step 4: Implement**

Create `backend/app/services/rul.py`:

```python
"""RUL via Weibull survival analysis, calibrated against real breakdown
history (DowntimeIncident where reason == "breakdown"). Refit is lazy,
on the first read each day - this codebase has no job scheduler at all
(confirmed: no APScheduler/celery/cron, and app.main's lifespan only starts
the realtime/sensor Kafka consumers), and already has a documented pattern
for exactly this problem: app.services.morning_brief's
_ensure_todays_snapshot. This mirrors that pattern rather than adding a new
scheduling mechanism."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from lifelines import WeibullFitter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.downtime_tracking import DowntimeIncident
from app.models.sensor import WeibullFit

_MIN_FAILURES_PER_MACHINE = 5
_MIN_FAILURES_FLEET = 5
_RUL_HORIZON_DAYS = 30


async def _breakdown_durations(db: AsyncSession, org_id: uuid.UUID, machine: str | None) -> tuple[list[float], list[bool]]:
    """Returns (durations_in_days, event_observed) pairs for lifelines:
    event_observed=True for a completed inter-breakdown interval,
    False (right-censored) for the still-open interval since the last
    breakdown (or since the earliest record if there's been none)."""
    query = select(DowntimeIncident).where(
        DowntimeIncident.org_id == org_id, DowntimeIncident.reason == "breakdown",
    )
    if machine is not None:
        query = query.where(DowntimeIncident.machine == machine)
    incidents = (await db.execute(query.order_by(DowntimeIncident.started_at))).scalars().all()

    if not incidents:
        return [], []

    durations: list[float] = []
    observed: list[bool] = []
    prev = incidents[0].started_at
    for incident in incidents[1:]:
        gap_days = (incident.started_at - prev).total_seconds() / 86400.0
        durations.append(max(gap_days, 0.01))
        observed.append(True)
        prev = incident.started_at

    now = datetime.now(timezone.utc)
    open_gap_days = (now - prev).total_seconds() / 86400.0
    durations.append(max(open_gap_days, 0.01))
    observed.append(False)  # right-censored: still running since the last breakdown

    return durations, observed


def _fit_weibull(durations: list[float], observed: list[bool]) -> tuple[float, float]:
    fitter = WeibullFitter()
    fitter.fit(durations, event_observed=observed)
    return float(fitter.rho_), float(fitter.lambda_)  # rho_ = shape k, lambda_ = scale


async def _compute_fit(db: AsyncSession, org_id: uuid.UUID, machine: str) -> tuple[float | None, float | None, int, int, str]:
    durations, observed = await _breakdown_durations(db, org_id, machine)
    n_failures = sum(observed)
    n_censored = len(observed) - n_failures

    if n_failures >= _MIN_FAILURES_PER_MACHINE:
        shape_k, scale_lambda = _fit_weibull(durations, observed)
        return shape_k, scale_lambda, n_failures, n_censored, "per_machine"

    fleet_durations, fleet_observed = await _breakdown_durations(db, org_id, None)
    fleet_failures = sum(fleet_observed)
    if fleet_failures >= _MIN_FAILURES_FLEET:
        shape_k, scale_lambda = _fit_weibull(fleet_durations, fleet_observed)
        return shape_k, scale_lambda, n_failures, n_censored, "fleet_pooled"

    return None, None, n_failures, n_censored, "insufficient_data"


async def get_or_refit(db: AsyncSession, org_id: uuid.UUID, machine: str) -> WeibullFit:
    today = datetime.now(timezone.utc).date()
    existing = (
        await db.execute(select(WeibullFit).where(WeibullFit.org_id == org_id, WeibullFit.machine == machine))
    ).scalar_one_or_none()
    if existing is not None and existing.fit_at.date() == today:
        return existing

    shape_k, scale_lambda, n_failures, n_censored, tier = await _compute_fit(db, org_id, machine)

    if existing is not None:
        existing.shape_k = shape_k
        existing.scale_lambda = scale_lambda
        existing.n_failures = n_failures
        existing.n_censored = n_censored
        existing.fit_tier = tier
        existing.fit_at = datetime.now(timezone.utc)
        await db.commit()
        return existing

    fit = WeibullFit(
        org_id=org_id, machine=machine, shape_k=shape_k, scale_lambda=scale_lambda,
        n_failures=n_failures, n_censored=n_censored, fit_tier=tier,
    )
    try:
        async with db.begin_nested():
            db.add(fit)
        await db.commit()
        return fit
    except IntegrityError:
        # A concurrent request refit this machine first - no scheduler
        # serializes these, same race the morning_brief pattern handles.
        await db.rollback()
        return (
            await db.execute(select(WeibullFit).where(WeibullFit.org_id == org_id, WeibullFit.machine == machine))
        ).scalar_one()


def failure_probability_30d(fit: WeibullFit) -> float | None:
    """Probability this machine has a breakdown within the next 30 days,
    conditioned on however long it's already run since its last breakdown
    (or since records began). None when the fit is insufficient_data - never
    fabricate a number with no calibration behind it."""
    if fit.shape_k is None or fit.scale_lambda is None:
        return None
    k, lam = fit.shape_k, fit.scale_lambda
    t = 0.0  # conditioning on "since the last breakdown" is handled by the
    # caller passing a fit whose durations already encode elapsed time;
    # the unconditional 30-day window from a fresh interval is the
    # reported figure - see rul.py module docstring in the design spec
    # for why a per-request "current age" isn't threaded through here.
    survival_now = pow(2.718281828459045, -pow(t / lam, k)) if lam > 0 else 1.0
    survival_30 = pow(2.718281828459045, -pow((t + _RUL_HORIZON_DAYS) / lam, k)) if lam > 0 else 1.0
    if survival_now <= 0:
        return 1.0
    return round(1.0 - (survival_30 / survival_now), 4)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rul.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/services/rul.py backend/tests/test_rul.py
git commit -m "feat: add Weibull RUL fitting, lazily refit per machine per day"
```

---

## Task 6: Alert rule integration — `cbm_anomaly` / `cbm_rul`

**Files:**
- Modify: `backend/app/services/alerts.py`
- Modify: `backend/app/api/routes/alerts.py`
- Test: `backend/tests/test_alerts_webhook.py` (add tests to the existing file)

**Interfaces:**
- Consumes: `SensorAnomaly` (Task 2), `app.services.rul.get_or_refit`/
  `failure_probability_30d` (Task 5).
- Produces: two new dispatchable `AlertRule.type` values, evaluated the same way
  `stream_metric` is today.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_alerts_webhook.py`. This file already defines `_register_and_login`,
`_h`, and a `webhook_server` fixture that yields `(server, handler)` — `handler.received` is
a list of the JSON bodies POSTed to it (see `test_scan_all_fires_webhook_with_live_triggered_state`
in that file for the exact pattern this mirrors). Add these two imports to the top of the
file alongside the existing ones:

```python
from datetime import datetime, timezone

from app.db import session as db_session
from app.models.sensor import SensorAnomaly, SensorReading
```

There is no `GET /auth/me`-style endpoint in this codebase (checked
`backend/app/api/routes/auth.py` — only `/register`, `/login`, `/mfa/*`, `/refresh`,
`/logout` exist). Get `org_id` the same way Task 4's `test_sensor_consumer.py` does:
create something via the API first (an `Integration` + `RealtimeStream`, which this test
needs anyway so the `cbm_anomaly` rule's machine has a real stream behind it), then look up
its `org_id` column directly.

```python
async def test_cbm_anomaly_rule_fires_webhook(client, webhook_server):
    server, handler = webhook_server
    url = f"http://127.0.0.1:{server.server_port}/andon"

    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )
    assert stream.status_code == 200, stream.text
    stream_id = stream.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        from app.models.realtime import RealtimeStream
        from sqlalchemy import select

        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()

        for _ in range(24):
            db.add(SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=10.0, ts=datetime.now(timezone.utc)))
        reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=50.0, ts=datetime.now(timezone.utc))
        db.add(reading)
        await db.flush()
        db.add(SensorAnomaly(org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1]))
        await db.commit()

    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "CBM anomaly", "type": "cbm_anomaly", "severity": "warning",
            "config": {"machine": "M1", "webhook_url": url},
        },
        headers=_h(token),
    )
    assert rule.status_code == 201, rule.text

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 1
    assert len(handler.received) == 1
    assert handler.received[0]["triggered"] is True
```

Add `import uuid` to the top of the file alongside the other new imports if it isn't
already imported there.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_alerts_webhook.py::test_cbm_anomaly_rule_fires_webhook -v`
Expected: FAIL — `400 Bad Request: Unknown alert type: cbm_anomaly`

- [ ] **Step 3: Add the evaluators**

In `backend/app/services/alerts.py`, add two new functions near `eval_stream_metric`
(after it, before the `_fire_webhook` section):

```python
async def eval_cbm_anomaly(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule) -> AlertTrigger:
    """Fires when a machine has any unacknowledged SensorAnomaly in the
    trailing window (default 24h, config['window_hours'])."""
    from app.models.sensor import SensorAnomaly

    cfg = rule.config
    machine = cfg.get("machine", "")
    window_hours = float(cfg.get("window_hours", 24))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    query = select(SensorAnomaly).where(
        SensorAnomaly.org_id == org_id, SensorAnomaly.detected_at >= cutoff,
        SensorAnomaly.acknowledged_at.is_(None),
    )
    if machine:
        query = query.where(SensorAnomaly.machine == machine)
    anomalies = (await db.execute(query)).scalars().all()

    triggered = len(anomalies) > 0
    machines = sorted({a.machine for a in anomalies})
    message = (
        f"{len(anomalies)} unacknowledged sensor anomal{'y' if len(anomalies) == 1 else 'ies'}"
        f" in the last {window_hours:g}h" if triggered
        else "No unacknowledged sensor anomalies."
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, float(len(anomalies)), 0.0,
        message, machines,
    )


async def eval_cbm_rul(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule) -> AlertTrigger:
    """Fires when a machine's 30-day failure probability crosses config['threshold']
    (default 0.65, matching the app-wide "high risk" convention)."""
    from app.services.rul import failure_probability_30d, get_or_refit

    cfg = rule.config
    machine = cfg.get("machine", "")
    threshold = float(cfg.get("threshold", 0.65))

    if not machine:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, "No machine configured.")

    fit = await get_or_refit(db, org_id, machine)
    prob = failure_probability_30d(fit)
    if prob is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            f"Insufficient failure history for {machine} to estimate RUL.",
        )

    triggered = prob >= threshold
    message = (
        f"{machine}: {prob * 100:.1f}% 30-day failure probability (threshold {threshold * 100:.0f}%)"
        if triggered else f"{machine}: {prob * 100:.1f}% 30-day failure probability (within threshold)"
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(prob, 4), threshold,
        message, [machine] if triggered else [],
    )
```

Add the required `timedelta` import to the existing `from datetime import datetime, timezone`
line at the top of the file (becomes `from datetime import datetime, timedelta, timezone`).

Modify `evaluate_rule` (currently lines 387-406) to dispatch the two new types before the
`_DATASET_EVALUATORS` lookup:

```python
async def evaluate_rule(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule,
    *, dataset: Dataset | None = None, con=None, rel: str | None = None,
) -> AlertTrigger:
    if rule.type == "stream_metric":
        return eval_stream_metric(rule)
    if rule.type == "cbm_anomaly":
        return await eval_cbm_anomaly(db, org_id, rule)
    if rule.type == "cbm_rul":
        return await eval_cbm_rul(db, org_id, rule)
    evaluator = _DATASET_EVALUATORS.get(rule.type)
    if evaluator is None:
        raise ValueError(f"Unknown alert type: {rule.type}")
    if rule.dataset_id is None:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, "No dataset configured.")
    if dataset is None:
        dataset = await ds_service.get_dataset_or_404(db, org_id, rule.dataset_id)
    if con is not None:
        return evaluator(rule, dataset, con, rel)
    con = ds_service.open_con()
    try:
        return evaluator(rule, dataset, con, ds_service.dataset_relation(dataset))
    finally:
        con.close()
```

Also modify `scan_all`'s dataset-grouping check (currently line 432) so `cbm_anomaly`/
`cbm_rul` rules go into `solo` (evaluated directly) rather than requiring a `dataset_id`,
same as `stream_metric` already does:

```python
    for rule in rules:
        if rule.type not in ("stream_metric", "cbm_anomaly", "cbm_rul") and rule.dataset_id is not None:
            by_dataset.setdefault(rule.dataset_id, []).append(rule)
        else:
            solo.append(rule)
```

- [ ] **Step 4: Update route-level validation**

In `backend/app/api/routes/alerts.py`, modify `_ALERT_TYPES` (line 17):

```python
_ALERT_TYPES = ("machine_downtime", "quality", "scrap", "inventory", "production_target", "stream_metric", "cbm_anomaly", "cbm_rul")
```

Modify the `dataset_id` requirement check in `create_rule` (line 86):

```python
    if payload.type not in ("stream_metric", "cbm_anomaly", "cbm_rul") and payload.dataset_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset_id is required for this alert type")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_alerts_webhook.py -v`
Expected: PASS (all tests in the file, including the new one)

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS (no regressions — `evaluate_rule`'s signature/behavior for existing types
is unchanged, only two new branches added before the existing dispatch).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/alerts.py backend/app/api/routes/alerts.py backend/tests/test_alerts_webhook.py
git commit -m "feat: add cbm_anomaly and cbm_rul alert rule types"
```

---

## Task 7: CBM API — fleet dashboard + machine detail

**Files:**
- Create: `backend/app/services/cbm.py`
- Create: `backend/app/api/routes/cbm.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/test_cbm_api.py`

**Interfaces:**
- Consumes: `SensorReading`/`SensorBaseline`/`SensorAnomaly` (Task 2),
  `app.services.rul.get_or_refit`/`failure_probability_30d` (Task 5), `RealtimeStream`
  (Task 1).
- Produces: `GET /cbm/fleet`, `GET /cbm/machines/{machine}` — consumed by Task 10/11
  (frontend).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_cbm_api.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading

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


async def test_fleet_dashboard_shows_stream_status_per_machine(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )
    assert stream.status_code == 200, stream.text

    res = await client.get("/cbm/fleet", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    machines = {m["machine"]: m for m in body["machines"]}
    assert machines["M1"]["status"] == "learning"  # no readings yet -> learning, not "active"


async def test_machine_detail_returns_readings_and_anomalies(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )

    async with db_session.async_session_factory() as db:
        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.machine == "M1"))
        ).scalar_one()
        now = datetime.now(timezone.utc)
        reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=50.0, ts=now)
        db.add(reading)
        await db.flush()
        db.add(SensorBaseline(org_id=org_id, machine="M1", sensor="vib_x", n=25, centerline=10.0, ucl=15.0, lcl=5.0, mr_bar=1.0))
        db.add(SensorAnomaly(org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1]))
        await db.commit()

    res = await client.get("/cbm/machines/M1", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sensors"]["vib_x"]["readings"][-1]["value"] == 50.0
    assert len(body["sensors"]["vib_x"]["anomalies"]) == 1


async def test_cbm_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    integ = await client.post(
        "/integrations", headers=_h(token_a),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    await client.post(
        "/realtime/streams", headers=_h(token_a),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )

    res_b = await client.get("/cbm/fleet", headers=_h(token_b))
    assert res_b.status_code == 200
    assert res_b.json()["machines"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_cbm_api.py -v`
Expected: FAIL — `404 Not Found` (no `/cbm` router registered yet)

- [ ] **Step 3: Implement the service layer**

Create `backend/app/services/cbm.py`:

```python
"""Fleet dashboard and per-machine detail for CBM - a pure read/rollup layer
over SensorReading/SensorBaseline/SensorAnomaly (Task 2/3/4) and WeibullFit
(Task 5). No new detection logic lives here."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.spc_calc import MIN_BASELINE
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.rul import failure_probability_30d, get_or_refit

_READINGS_PER_SENSOR = 200
_STALE_AFTER_MINUTES = 15


def _risk_level(prob: float | None) -> str:
    if prob is None:
        return "unknown"
    if prob >= 0.65:
        return "high"
    if prob >= 0.35:
        return "medium"
    return "low"


async def fleet_dashboard(db: AsyncSession, org_id: uuid.UUID) -> list[dict]:
    streams = (
        await db.execute(
            select(RealtimeStream).where(RealtimeStream.org_id == org_id, RealtimeStream.machine.is_not(None))
        )
    ).scalars().all()

    out = []
    for stream in streams:
        machine = stream.machine
        baselines = (
            await db.execute(select(SensorBaseline).where(SensorBaseline.org_id == org_id, SensorBaseline.machine == machine))
        ).scalars().all()

        # No baselines yet (no readings scored) or any sensor still under
        # MIN_BASELINE readings -> still learning. Once every mapped sensor
        # has a baseline past MIN_BASELINE, the machine is "active".
        if not baselines or any(b.n < MIN_BASELINE for b in baselines):
            status = "learning"
        else:
            status = "active"

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        active_anomalies = (
            await db.execute(
                select(SensorAnomaly).where(
                    SensorAnomaly.org_id == org_id, SensorAnomaly.machine == machine,
                    SensorAnomaly.detected_at >= cutoff, SensorAnomaly.acknowledged_at.is_(None),
                )
            )
        ).scalars().all()

        last_reading = (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.org_id == org_id, SensorReading.machine == machine)
                .order_by(SensorReading.ts.desc()).limit(1)
            )
        ).scalar_one_or_none()

        fit = await get_or_refit(db, org_id, machine)
        prob = failure_probability_30d(fit)

        out.append({
            "machine": machine, "status": status, "risk_level": _risk_level(prob),
            "failure_probability_30d": prob, "active_anomalies": len(active_anomalies),
            "last_reading_at": last_reading.ts.isoformat() if last_reading else None,
        })

    out.sort(key=lambda m: {"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(m["risk_level"], 4))
    return out


async def machine_detail(db: AsyncSession, org_id: uuid.UUID, machine: str) -> dict:
    baselines = (
        await db.execute(select(SensorBaseline).where(SensorBaseline.org_id == org_id, SensorBaseline.machine == machine))
    ).scalars().all()

    sensors: dict[str, dict] = {}
    for baseline in baselines:
        readings = (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.org_id == org_id, SensorReading.machine == machine, SensorReading.sensor == baseline.sensor)
                .order_by(SensorReading.ts.desc()).limit(_READINGS_PER_SENSOR)
            )
        ).scalars().all()
        readings = list(reversed(readings))

        anomalies = (
            await db.execute(
                select(SensorAnomaly)
                .where(SensorAnomaly.org_id == org_id, SensorAnomaly.machine == machine, SensorAnomaly.sensor == baseline.sensor)
                .order_by(SensorAnomaly.detected_at.desc()).limit(50)
            )
        ).scalars().all()

        sensors[baseline.sensor] = {
            "centerline": baseline.centerline, "ucl": baseline.ucl, "lcl": baseline.lcl, "n": baseline.n,
            "readings": [{"value": r.value, "ts": r.ts.isoformat()} for r in readings],
            "anomalies": [
                {
                    "id": str(a.id), "violations": a.violations, "detected_at": a.detected_at.isoformat(),
                    "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                }
                for a in anomalies
            ],
        }

    fit = await get_or_refit(db, org_id, machine)
    prob = failure_probability_30d(fit)

    return {
        "machine": machine, "risk_level": _risk_level(prob), "failure_probability_30d": prob,
        "fit_tier": fit.fit_tier, "sensors": sensors,
    }


async def acknowledge_anomaly(db: AsyncSession, org_id: uuid.UUID, anomaly_id: uuid.UUID) -> SensorAnomaly | None:
    anomaly = (
        await db.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == org_id, SensorAnomaly.id == anomaly_id))
    ).scalar_one_or_none()
    if anomaly is None:
        return None
    anomaly.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return anomaly
```

- [ ] **Step 4: Implement the routes**

Create `backend/app/api/routes/cbm.py`:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import cbm as service

router = APIRouter(prefix="/cbm", tags=["cbm"])


@router.get("/fleet")
async def fleet_dashboard(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    machines = await service.fleet_dashboard(db, current.org_id)
    return {"machines": machines}


@router.get("/machines/{machine}")
async def machine_detail(machine: str, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.machine_detail(db, current.org_id, machine)


@router.post("/anomalies/{anomaly_id}/acknowledge")
async def acknowledge_anomaly(
    anomaly_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    anomaly = await service.acknowledge_anomaly(db, current.org_id, anomaly_id)
    if anomaly is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomaly not found")
    return {"id": str(anomaly.id), "acknowledged_at": anomaly.acknowledged_at.isoformat()}
```

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, find where other routers are `app.include_router(...)`'d
(alongside `predictive.router`/`realtime.router`) and add:

```python
from app.api.routes import cbm as cbm_routes
```

```python
app.include_router(cbm_routes.router)
```

(Match the exact existing style — read the surrounding lines in `main.py` first to see
whether routers are imported individually or via a shared `from app.api.routes import (...)`
block, and follow that.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_cbm_api.py -v`
Expected: PASS (all 3 tests)

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/cbm.py backend/app/api/routes/cbm.py backend/app/main.py backend/tests/test_cbm_api.py
git commit -m "feat: add CBM fleet dashboard and machine detail API"
```

---

## Task 8: Backend cleanup — delete OEE-based predictive analysis

**Files:**
- Modify: `backend/app/services/predictive.py`
- Modify: `backend/app/api/routes/predictive.py`
- Modify: `backend/tests/test_predictive.py`

**Interfaces:**
- Removes: `analyze()`, `_ml_analyze()`, `_stat_analyze()`, `_rul_days()`, `_top_driver()`,
  `_linreg_slope()`, `_recent_slope()`, `_std()`, and the `MachineRisk`/`ModelMeta`/
  `MaintenanceRec`/`CostSummary`/`PredictiveResult` dataclasses from `predictive.py`; the
  `POST /datasets/{dataset_id}/predictive/analyze` route and its Pydantic schemas from
  `routes/predictive.py`.
- Keeps unchanged: `list_schedules`/`create_schedule`/`bulk_create_schedules`/
  `get_schedule_or_404`/`update_schedule`/`delete_schedule` and the `/predictive/schedules*`
  routes — the schedule CRUD is a generic action queue indifferent to how risk was computed.

- [ ] **Step 1: Remove the obsolete tests**

In `backend/tests/test_predictive.py`, delete `test_analyze_flags_low_oee_machine_high_risk`
and `test_analyze_rejects_missing_columns` (and the `_upload`/`PREDICTIVE_CSV` helpers if
nothing else in the file uses them — check first). Keep `test_schedule_crud`,
`test_schedule_not_found`, and `test_predictive_tenant_isolation` unchanged.

- [ ] **Step 2: Run the trimmed test file to confirm it still passes**

Run: `cd backend && python -m pytest tests/test_predictive.py -v`
Expected: PASS (the 3 remaining schedule tests)

- [ ] **Step 3: Delete the OEE analysis code from the service**

In `backend/app/services/predictive.py`, delete everything from the top of the file down
through the end of `analyze()` (currently lines 1-507) — i.e. every import, constant,
dataclass, and function used only by the OEE analysis path. Keep only the
`# ─── Maintenance schedule CRUD` section (currently lines 510-559) and the imports it
actually needs:

```python
"""Maintenance schedule CRUD - the recommendation/action queue used by both
the (now-removed) OEE-based risk scoring and the CBM fleet dashboard
(app.services.cbm). Kept generic: it doesn't care how a recommendation's
priority/action was computed."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance_schedule import MaintenanceSchedule


async def list_schedules(db: AsyncSession, org_id: uuid.UUID) -> list[MaintenanceSchedule]:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id).order_by(MaintenanceSchedule.created_at.desc())
    )
    return list(result.scalars().all())


async def create_schedule(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, **fields) -> MaintenanceSchedule:
    item = MaintenanceSchedule(org_id=org_id, created_by=created_by, **fields)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def bulk_create_schedules(db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, items: list[dict]) -> list[MaintenanceSchedule]:
    rows = [MaintenanceSchedule(org_id=org_id, created_by=created_by, **item) for item in items]
    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


async def get_schedule_or_404(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> MaintenanceSchedule:
    result = await db.execute(
        select(MaintenanceSchedule).where(MaintenanceSchedule.org_id == org_id, MaintenanceSchedule.id == schedule_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ValueError("Maintenance schedule item not found")
    return item


async def update_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID, patch: dict) -> MaintenanceSchedule:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    for key, value in patch.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


async def delete_schedule(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    item = await get_schedule_or_404(db, org_id, schedule_id)
    await db.delete(item)
    await db.commit()
```

- [ ] **Step 4: Delete the OEE analysis route**

In `backend/app/api/routes/predictive.py`, delete the `analyze_router` (its declaration,
the `PredictiveRequest`/`MachineRiskResponse`/`ModelMetaResponse`/`MaintenanceRecResponse`/
`CostSummaryResponse`/`PredictiveResponse` schemas, and the `analyze_predictive` handler —
currently lines 1-92 down through the maintenance-schedule-CRUD comment divider). Keep the
`router` (schedule CRUD) section unchanged. The trimmed file's imports become:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import predictive as service

router = APIRouter(prefix="/predictive", tags=["predictive"])
```

Check `backend/app/main.py` for wherever `analyze_router` was registered (it's a second,
separate `APIRouter` from `router` in the original file — search for
`predictive.analyze_router` or similar) and remove that registration line, keeping the
`predictive.router` (schedules) registration.

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS — confirms nothing else in the codebase imports the deleted
`analyze`/`MachineRisk`/etc. symbols. If anything fails with an import error, grep the
whole backend for the deleted symbol names before assuming the test itself is wrong.

Run: `cd backend && python -m alembic upgrade head` (should already be a no-op — no schema
change in this task, just confirming nothing broke).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/predictive.py backend/app/api/routes/predictive.py backend/app/main.py backend/tests/test_predictive.py
git commit -m "refactor: remove OEE-based predictive analysis, replaced by CBM"
```

---

## Task 9: Frontend — `Realtime.tsx` gains machine + sensor map fields

**Files:**
- Modify: `frontend/src/pages/Realtime.tsx`

**Interfaces:**
- Consumes: `POST /realtime/streams` now accepting `machine`/`sensor_map` (Task 1).

- [ ] **Step 1: Add `machine` and a dynamic sensor-map editor to the "New stream" form**

In `frontend/src/pages/Realtime.tsx`, modify `emptyForm()` (currently lines 55-62):

```tsx
function emptyForm() {
  return {
    integration_id: "",
    name: "",
    machine: "",
    interval_seconds: 5,
    columnMap: Object.fromEntries(COLUMN_MAP_FIELDS.map((f) => [f.key, ""])) as Record<string, string>,
    sensorMap: [] as { name: string; field: string }[],
  };
}
```

Modify the `create` mutation (currently lines 132-154) to send the new fields:

```tsx
  const create = useMutation({
    mutationFn: async () => {
      const column_map = Object.fromEntries(Object.entries(form.columnMap).filter(([, v]) => v.trim()));
      const sensor_map = Object.fromEntries(
        form.sensorMap.filter((s) => s.name.trim() && s.field.trim()).map((s) => [s.name.trim(), s.field.trim()]),
      );
      const res = await apiFetch("/realtime/streams", {
        method: "POST",
        body: JSON.stringify({
          integration_id: form.integration_id,
          name: form.name,
          machine: form.machine.trim() || null,
          interval_seconds: form.interval_seconds,
          column_map,
          sensor_map,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create stream");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["realtime-streams"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });
```

Add a machine field and a dynamic sensor-map row editor to the dialog form, right after the
existing "Column map" `Field` block (currently ending around line 294):

```tsx
          <Field label="Machine (for CBM, optional)" hint="Set this to feed live sensor data from this stream into Predictive Maintenance's condition-based monitoring.">
            <Input value={form.machine} onChange={(e) => setForm((f) => ({ ...f, machine: e.target.value }))} />
          </Field>
          {form.machine && (
            <Field
              label="Sensor map"
              hint="Sensor name → field to read from each polled row. For an OPC-UA stream (one node per stream), map a single sensor name to the field 'value'."
            >
              <div className="space-y-2">
                {form.sensorMap.map((s, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      placeholder="Sensor name (e.g. vib_x)"
                      value={s.name}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, name: e.target.value } : row)),
                        }))
                      }
                    />
                    <Input
                      placeholder="Source field name"
                      value={s.field}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, field: e.target.value } : row)),
                        }))
                      }
                    />
                    <Button
                      type="button" variant="secondary" size="sm"
                      onClick={() => setForm((f) => ({ ...f, sensorMap: f.sensorMap.filter((_, j) => j !== i) }))}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                <Button
                  type="button" variant="secondary" size="sm"
                  onClick={() => setForm((f) => ({ ...f, sensorMap: [...f.sensorMap, { name: "", field: "" }] }))}
                >
                  Add sensor
                </Button>
              </div>
            </Field>
          )}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: builds with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Realtime.tsx
git commit -m "feat: add machine + sensor map fields to the New Stream form"
```

---

## Task 10: Frontend — `Predictive.tsx` fleet dashboard rewrite

**Files:**
- Modify: `frontend/src/pages/Predictive.tsx` (full rewrite of the top-level export;
  `ScheduleTable` is kept as-is per the spec)

**Interfaces:**
- Consumes: `GET /cbm/fleet` (Task 7).
- Produces: the `Predictive` page's landing view, which links into Task 11's machine
  detail view.

- [ ] **Step 1: Replace the dataset/column-picker flow with the fleet dashboard**

Rewrite `frontend/src/pages/Predictive.tsx`. Keep the existing `ScheduleTable` function
(currently lines 66-129) completely unchanged — the spec keeps schedule CRUD as-is. Replace
everything else (the `Predictive` export, its state/mutations, and the
`MachineRisk`/`MaintenanceRec`/`PredictiveResult`/`ScheduleItem` interfaces at the top) with:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { MachineDetail } from "./predictive/MachineDetail";

interface FleetMachine {
  machine: string;
  status: "learning" | "active";
  risk_level: "high" | "medium" | "low" | "unknown";
  failure_probability_30d: number | null;
  active_anomalies: number;
  last_reading_at: string | null;
}

const RISK_VARIANT: Record<string, "danger" | "warning" | "success" | "neutral"> = {
  high: "danger", medium: "warning", low: "success", unknown: "neutral",
};

const STATUS_LABEL: Record<string, string> = { learning: "Learning baseline", active: "Active" };

function ScheduleTable() {
  // ... unchanged from the existing file - see Task spec: keep this function as-is.
}

export function Predictive() {
  const [selectedMachine, setSelectedMachine] = useState<string | null>(null);

  const fleet = useQuery<{ machines: FleetMachine[] }>({
    queryKey: ["cbm-fleet"],
    queryFn: async () => {
      const res = await apiFetch("/cbm/fleet");
      if (!res.ok) throw new Error("Failed to load fleet status");
      return res.json();
    },
    refetchInterval: 15000,
  });

  if (selectedMachine) {
    return <MachineDetail machine={selectedMachine} onBack={() => setSelectedMachine(null)} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Predictive Maintenance"
        description="Condition-based monitoring: live sensor anomaly detection and Weibull-calibrated remaining-useful-life, per machine."
      />

      {fleet.data && fleet.data.machines.length === 0 ? (
        <EmptyState
          icon={<Radio />}
          title="No machines connected"
          description="Set a machine and sensor map on a realtime stream (Realtime page) to start monitoring it here."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Fleet health</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Machine</TH>
                    <TH>Status</TH>
                    <TH>Risk</TH>
                    <TH>30-day failure probability</TH>
                    <TH>Active anomalies</TH>
                    <TH>Last reading</TH>
                  </TR>
                </THead>
                <TBody>
                  {fleet.data?.machines.map((m) => (
                    <TR key={m.machine} className="cursor-pointer" onClick={() => setSelectedMachine(m.machine)}>
                      <TD className="font-medium">{m.machine}</TD>
                      <TD className="text-muted-foreground">{STATUS_LABEL[m.status] ?? m.status}</TD>
                      <TD>
                        <Badge variant={RISK_VARIANT[m.risk_level] ?? "neutral"}>{m.risk_level}</Badge>
                      </TD>
                      <TD>{m.failure_probability_30d != null ? `${(m.failure_probability_30d * 100).toFixed(1)}%` : "—"}</TD>
                      <TD>{m.active_anomalies}</TD>
                      <TD className="text-muted-foreground">
                        {m.last_reading_at ? new Date(m.last_reading_at).toLocaleString() : "No readings yet"}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </CardContent>
        </Card>
      )}

      <ScheduleTable />
    </div>
  );
}
```

Restore the real `ScheduleTable` body (the placeholder comment above stands in for the
existing 64 lines already in the file at lines 66-129 — copy them verbatim, do not
re-derive them).

- [ ] **Step 2: Type-check (expect a missing-module error for now)**

Run: `cd frontend && npm run build`
Expected: FAILS — `Cannot find module './predictive/MachineDetail'`. This confirms the
dashboard half compiles cleanly against everything except the not-yet-created detail
component, which Task 11 adds next.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Predictive.tsx
git commit -m "refactor: rewrite Predictive.tsx as a CBM fleet dashboard"
```

(This task intentionally leaves the build red — Task 11 makes it pass. If running these
tasks via subagent-driven-development with a review gate between tasks, note this
explicitly in the handoff so the reviewer doesn't flag the failing build as a defect in
this task.)

---

## Task 11: Frontend — machine detail view (trend chart + RUL + anomalies)

**Files:**
- Create: `frontend/src/pages/predictive/MachineDetail.tsx`

**Interfaces:**
- Consumes: `GET /cbm/machines/{machine}` (Task 7), `POST /cbm/anomalies/{id}/acknowledge`
  (Task 7), the existing `EChart`/`controlChart` (`frontend/src/pages/analytics/Spc.tsx`
  and `frontend/src/pages/analytics/shared.tsx`).
- Produces: `MachineDetail` component, imported by Task 10's `Predictive.tsx`.

- [ ] **Step 1: Implement**

Create `frontend/src/pages/predictive/MachineDetail.tsx`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { EChart } from "@/pages/analytics/shared";
import { controlChart, NELSON_RULE_DESCRIPTIONS, type SpcPoint } from "@/pages/analytics/Spc";

interface SensorAnomaly {
  id: string;
  violations: number[];
  detected_at: string;
  acknowledged_at: string | null;
}

interface SensorDetail {
  centerline: number | null;
  ucl: number | null;
  lcl: number | null;
  n: number;
  readings: { value: number; ts: string }[];
  anomalies: SensorAnomaly[];
}

interface MachineDetailResult {
  machine: string;
  risk_level: "high" | "medium" | "low" | "unknown";
  failure_probability_30d: number | null;
  fit_tier: "per_machine" | "fleet_pooled" | "insufficient_data";
  sensors: Record<string, SensorDetail>;
}

const RISK_VARIANT: Record<string, "danger" | "warning" | "success" | "neutral"> = {
  high: "danger", medium: "warning", low: "success", unknown: "neutral",
};

const FIT_TIER_LABEL: Record<string, string> = {
  per_machine: "Calibrated from this machine's breakdown history",
  fleet_pooled: "Calibrated from fleet-wide breakdown history (not enough data for this machine alone)",
  insufficient_data: "Not enough breakdown history to estimate",
};

function toSpcPoints(sensor: SensorDetail): SpcPoint[] {
  const { centerline, ucl, lcl } = sensor;
  return sensor.readings.map((r) => ({
    label: new Date(r.ts).toLocaleString(),
    value: r.value,
    in_control: centerline == null || ucl == null || lcl == null ? true : r.value <= ucl && r.value >= lcl,
  }));
}

export function MachineDetail({ machine, onBack }: { machine: string; onBack: () => void }) {
  const qc = useQueryClient();

  const detail = useQuery<MachineDetailResult>({
    queryKey: ["cbm-machine", machine],
    queryFn: async () => {
      const res = await apiFetch(`/cbm/machines/${encodeURIComponent(machine)}`);
      if (!res.ok) throw new Error("Failed to load machine detail");
      return res.json();
    },
    refetchInterval: 15000,
  });

  const acknowledge = useMutation({
    mutationFn: async (anomalyId: string) => {
      const res = await apiFetch(`/cbm/anomalies/${anomalyId}/acknowledge`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to acknowledge");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cbm-machine", machine] }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={machine}
        description="Condition-based monitoring detail"
        actions={
          <Button variant="secondary" onClick={onBack}>
            <ArrowLeft className="mr-2 size-4" /> Back to fleet
          </Button>
        }
      />

      {detail.data && (
        <Card>
          <CardHeader>
            <CardTitle>
              Remaining useful life{" "}
              <Badge variant={RISK_VARIANT[detail.data.risk_level] ?? "neutral"}>{detail.data.risk_level}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">
              {detail.data.failure_probability_30d != null
                ? `${(detail.data.failure_probability_30d * 100).toFixed(1)}% chance of failure in the next 30 days`
                : "Not enough breakdown history to estimate RUL"}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{FIT_TIER_LABEL[detail.data.fit_tier]}</p>
          </CardContent>
        </Card>
      )}

      {detail.data &&
        Object.entries(detail.data.sensors).map(([sensor, s]) => (
          <Card key={sensor}>
            <CardHeader>
              <CardTitle>{sensor}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {s.centerline != null && s.ucl != null && s.lcl != null ? (
                <EChart
                  height={280}
                  option={controlChart(toSpcPoints(s), (p) => p.value, s.centerline, s.ucl, s.lcl, sensor)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Learning baseline — needs at least 20 readings.</p>
              )}

              {s.anomalies.length > 0 && (
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Detected</TH>
                        <TH>Rule violations</TH>
                        <TH>Status</TH>
                        <TH />
                      </TR>
                    </THead>
                    <TBody>
                      {s.anomalies.map((a) => (
                        <TR key={a.id}>
                          <TD className="text-muted-foreground">{new Date(a.detected_at).toLocaleString()}</TD>
                          <TD>{a.violations.map((v) => NELSON_RULE_DESCRIPTIONS[v] ?? `Rule ${v}`).join("; ")}</TD>
                          <TD>
                            <Badge variant={a.acknowledged_at ? "neutral" : "warning"}>
                              {a.acknowledged_at ? "Acknowledged" : "Open"}
                            </Badge>
                          </TD>
                          <TD>
                            {!a.acknowledged_at && (
                              <Button size="sm" variant="secondary" loading={acknowledge.isPending} onClick={() => acknowledge.mutate(a.id)}>
                                Acknowledge
                              </Button>
                            )}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
              )}
            </CardContent>
          </Card>
        ))}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: builds with no TypeScript errors. If `controlChart`/`NELSON_RULE_DESCRIPTIONS`/
`SpcPoint` aren't already exported from `frontend/src/pages/analytics/Spc.tsx`, check that
file — `controlChart` and `NELSON_RULE_DESCRIPTIONS` are already `export`ed there; `SpcPoint`
is already an exported `interface`. If `EChart` isn't exported from
`frontend/src/pages/analytics/shared.tsx`, check that file too — it is, alongside
`EngineScreen`/`AXIS_LABEL`/etc.

- [ ] **Step 3: Manual verification**

Since this repo has no component-render test framework (only `vitest run` for pure-logic
tests — confirmed during planning research), verify this view by running the app:
`npm run dev` in `frontend/`, log in, go to Predictive Maintenance, and confirm the fleet
table renders (even empty) and clicking a row (once a machine with a stream exists, from
Task 9's form) shows the detail view without a console error. This is a manual check, not
an automated one — say so explicitly rather than claiming automated coverage that doesn't
exist.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/predictive/MachineDetail.tsx
git commit -m "feat: add CBM machine detail view (trend chart, RUL, anomalies)"
```

---

## Post-plan checklist (do after Task 11)

- [ ] Run the full backend suite once more: `cd backend && python -m pytest tests/ -v`
- [ ] Run the frontend build once more: `cd frontend && npm run build`
- [ ] Confirm `docs/superpowers/specs/2026-07-18-cbm-predictive-maintenance-design.md`'s
  "Explicitly Out of Scope" section still accurately describes what wasn't built (ML
  scorer seam, multi-signal correlation, downsampling, automatic baseline-shift detection,
  score fusion) — no task above builds any of those, so no update should be needed, but
  verify rather than assume.
- [ ] Update `frontend/src/pages/analytics/PlantSummary.tsx` or wherever the app's nav
  menu is defined only if the Realtime page's "New stream" dialog changes (Task 9) affect
  any copy referencing "column map" as the only mapping option — check
  `frontend/src/pages/Realtime.tsx`'s `PageHeader` description text for accuracy after
  Task 9.
