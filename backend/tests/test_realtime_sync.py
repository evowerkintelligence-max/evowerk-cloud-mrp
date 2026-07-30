"""Tests for the DB-backed realtime pipeline: sample model, collector
dedup, cursor sync, and retention. Replaces the broker-era cache/producer
tests."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
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


async def test_window_cap_carries_surplus_rows_into_next_window(client, monkeypatch):
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    # Module-level constant, read at call time inside _poll_once, so patching
    # it here beats generating 1000+ rows.
    monkeypatch.setattr(realtime_service, "_MAX_ROWS_PER_WINDOW", 3)
    batch = [{"units": i, "scrap": 0} for i in range(5)]
    c = _collector_for(stream, [batch])  # same over-cap batch on every poll
    await c._seed_counters()
    await c._poll_once()
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        samples = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
    # Window 2 is the REMAINDER, not zero: the two rows past the cap are held
    # back as still-new and drain here instead of being marked seen and lost.
    assert [s.rows_in_window for s in samples] == [3, 2]
    assert [s.rows_collected for s in samples] == [3, 5]
    # The two windows partition the batch in order: no duplicates, no omissions.
    assert samples[0].raw_rows + samples[1].raw_rows == batch


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


async def test_fetch_error_does_not_reingest_the_whole_source(client):
    """A transient fetch failure keeps the dedup state (_prev): the poll after
    it must see the unchanged source table as already-seen, not re-ingest
    every row as new."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    class _Boom:
        def fetch_all(self):
            raise RuntimeError("source down")

    batch = [{"units": 10, "scrap": 1}, {"units": 20, "scrap": 2}]
    c = _collector_for(stream, [batch])
    await c._seed_counters()
    await c._poll_once()  # window 1: both rows are new

    c._conn = _Boom()
    await c._poll_once()  # window 2: error, no rows, dedup state preserved

    c._conn = _StubConnector([batch])  # same table as window 1, nothing added
    await c._poll_once()

    async with db_session.async_session_factory() as db:
        samples = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
    assert [s.status for s in samples] == ["running", "error", "running"]
    assert [s.rows_in_window for s in samples] == [2, 0, 0]  # window 3 is NOT a re-ingest
    assert [s.rows_collected for s in samples] == [2, 2, 2]


async def test_failed_write_does_not_mark_rows_as_seen(client, monkeypatch):
    """A DB write that blows up (deadlock, statement timeout, an ingest_rows
    integrity error) must not advance the dedup state: those rows never
    reached raw_rows or sensor_readings, so the next window has to re-offer
    them instead of dropping them forever."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db, machine="M1", sensor_map={"vib_x": "vibration_x_mm_s"})

    batch1 = [{"vibration_x_mm_s": 1.0}]
    batch2 = [{"vibration_x_mm_s": 1.0}, {"vibration_x_mm_s": 2.0}]
    c = _collector_for(stream, [batch1, batch2])
    await c._seed_counters()
    await c._poll_once()  # window 1 commits normally

    real_ingest = realtime_service.ingest_rows
    failing = {"on": True}

    async def _maybe_boom(*args, **kwargs):
        if failing["on"]:
            raise RuntimeError("deadlock detected")
        return await real_ingest(*args, **kwargs)

    # monkeypatch.undo() is not usable here: the `client` fixture shares this
    # same monkeypatch instance and undoing it would restore the real
    # (non-test) session factory.
    monkeypatch.setattr(realtime_service, "ingest_rows", _maybe_boom)
    # _run swallows this (logger.exception) and polls again, so the collector
    # survives; what matters is the state it survives with.
    with pytest.raises(RuntimeError):
        await c._poll_once()

    failing["on"] = False
    await c._poll_once()  # window 3: SAME source table, the lost row must reappear

    async with db_session.async_session_factory() as db:
        samples = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
        readings = (await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))).scalars().all()
    # The failed window's own write rolled back (no rows, no counter movement);
    # what stands in its place is a rows-free error marker, and the lost row is
    # recovered by the next window rather than silently dropped.
    assert [s.status for s in samples] == ["running", "error", "running"]
    assert [s.rows_in_window for s in samples] == [1, 0, 1]
    assert [s.rows_collected for s in samples] == [1, 1, 2]
    assert samples[2].raw_rows == [{"vibration_x_mm_s": 2.0}]
    assert sorted(r.value for r in readings) == [1.0, 2.0]


async def test_failed_write_surfaces_as_an_error_instead_of_a_frozen_stream(client, monkeypatch):
    """A write that blows up must not just vanish: with no sample written the
    UI would sit on the last good window forever. The failure is recorded on
    the stream row AND as a rows-free error sample, so /snapshot, /samples and
    the cursor all move."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db, machine="M1", sensor_map={"vib_x": "vibration_x_mm_s"})
        stream.desired_status = "running"  # else the snapshot overlays "stopped"
        await db.commit()

    async def _boom(*args, **kwargs):
        raise RuntimeError("statement timeout")

    monkeypatch.setattr(realtime_service, "ingest_rows", _boom)
    c = _collector_for(stream, [[{"vibration_x_mm_s": 1.0}]])
    await c._seed_counters()
    with pytest.raises(RuntimeError):
        await c._poll_once()

    async with db_session.async_session_factory() as db:
        refreshed = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == stream.id))
        ).scalar_one()
        snap = await realtime_service.latest_snapshot(db, refreshed)
    assert "statement timeout" in refreshed.last_error
    assert snap["status"] == "error"
    assert "statement timeout" in snap["error"]
    assert snap["rows_in_window"] == 0  # the error marker carries no rows


async def test_recent_rows_pages_past_the_first_page(client, monkeypatch):
    """recent_rows must actually reach `n` rows, not stop at one page: the
    retrain path asks for 10_000 rows and a stream of small windows only gets
    there by paginating the sample scan."""
    await _register_and_login(client)
    monkeypatch.setattr(realtime_service, "_SAMPLE_SCAN_PAGE", 5)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)
        for i in range(12):  # more than two pages of 5
            db.add(RealtimeSample(
                org_id=stream.org_id, stream_id=stream.id, status="running",
                rows_in_window=1, rows_collected=i + 1, duration_s=i,
                metrics={}, raw_rows=[{"units": i}],
            ))
        await db.commit()

        rows = await realtime_service.recent_rows(db, stream.id, 12)

    # Every sample, oldest first: the newest page alone would be [7..11].
    assert rows == [{"units": i} for i in range(12)]

    async with db_session.async_session_factory() as db:
        capped = await realtime_service.recent_rows(db, stream.id, 3)
    assert capped == [{"units": 9}, {"units": 10}, {"units": 11}]  # at most n, newest n


async def test_running_stream_with_an_old_sample_reports_stale(client):
    """A dead collector leaves its last sample saying "running" forever, so
    liveness is derived from the newest sample's age at read time."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db, interval_seconds=5)
        stream.desired_status = "running"
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            ts=datetime.now(timezone.utc) - timedelta(minutes=10),
            rows_in_window=1, rows_collected=9, duration_s=1, metrics={"throughput": 1.0},
            raw_rows=[],
        ))
        await db.commit()
        stale = await realtime_service.latest_snapshot(db, stream)

    assert stale["status"] == "stale"
    assert stale["rows_collected"] == 9  # counters and metrics untouched
    assert stale["metrics"]["throughput"] == 1.0

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == stream.id))
        ).scalar_one()
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=1, rows_collected=10, duration_s=2, metrics={}, raw_rows=[],
        ))
        await db.commit()
        fresh = await realtime_service.latest_snapshot(db, stream)
    assert fresh["status"] == "running"


async def test_dedup_state_rehydrates_from_samples_on_restart(client):
    """A restart must not re-ingest the whole source table: _seed_counters
    rebuilds _prev from the rows already persisted in this stream's samples."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db, machine="M1", sensor_map={"vib_x": "vibration_x_mm_s"})

    batch = [{"vibration_x_mm_s": 10.0}, {"vibration_x_mm_s": 11.0}]
    c = _collector_for(stream, [batch])
    await c._seed_counters()
    await c._poll_once()

    restarted = _collector_for(stream, [batch])  # fresh process, empty _prev
    await restarted._seed_counters()
    await restarted._poll_once()

    async with db_session.async_session_factory() as db:
        samples = (
            await db.execute(
                select(RealtimeSample).where(RealtimeSample.stream_id == stream.id).order_by(RealtimeSample.id)
            )
        ).scalars().all()
        readings = (await db.execute(select(SensorReading).where(SensorReading.machine == "M1"))).scalars().all()
    assert [s.rows_in_window for s in samples] == [2, 0]  # not a full re-ingest
    assert [s.rows_collected for s in samples] == [2, 2]
    assert len(readings) == 2


async def test_stream_lock_degrades_off_postgres(client, monkeypatch):
    """pg_try_advisory_lock does not exist on the SQLite test DB (nor on any
    non-Postgres deployment): the collector must collect unlocked rather than
    refuse to start."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)

    key = realtime_service._advisory_lock_key(stream.id)
    assert -(2**63) <= key < 2**63  # Postgres advisory locks take int8 only

    may_collect, lock_db = await realtime_service._try_lock_stream(stream.id)
    assert may_collect is True
    assert lock_db is None

    # And the real _run path gets past the lock gate and writes a sample.
    monkeypatch.setattr(
        realtime_service, "get_connector", lambda *a, **k: _StubConnector([[{"units": 1, "scrap": 0}]])
    )
    c = _collector_for(stream, [[]])
    c.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            async with db_session.async_session_factory() as db:
                samples = (
                    await db.execute(select(RealtimeSample).where(RealtimeSample.stream_id == stream.id))
                ).scalars().all()
            if samples:
                break
    finally:
        await c.stop()
    assert [s.rows_in_window for s in samples] == [1]


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


async def test_snapshot_with_no_sample_still_carries_external_metrics(client):
    """A stream with no sample row but a published external metric (an AutoML
    risk score written before the collector's first window, or after
    _maybe_prune deleted the samples and left the metric rows) must still be
    evaluated by alerts.py, whose guard is `ts is None AND no metrics`. Both
    halves matter: this snapshot satisfies only the first."""
    await _register_and_login(client)
    async with db_session.async_session_factory() as db:
        stream = await _seed_stream(db)
        await realtime_service.set_external_metric(db, stream.id, "automl_risk_score", 0.9)
        await db.commit()

        snap = await realtime_service.latest_snapshot(db, stream)

    assert snap["ts"] is None  # no sample row at all
    assert snap["metrics"]["automl_risk_score"] == 0.9  # external metric merged in
    # The alert guard's own condition: "not running, nothing to evaluate" must
    # be False here, so evaluation proceeds.
    assert not (snap.get("ts") is None and not snap.get("metrics"))


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
