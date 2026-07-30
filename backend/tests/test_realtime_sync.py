"""Tests for the DB-backed realtime pipeline: sample model, collector
dedup, cursor sync, and retention. Replaces the broker-era cache/producer
tests."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session as db_session
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream
from app.models.sensor import SensorReading
from app.models.user import User
from app.services import realtime as realtime_service
from app.services.realtime import StreamCollector, new_rows_since_last_poll

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
