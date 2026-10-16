from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import session as db_session_module
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick
from app.models.tenant import Org
from app.models.user import User
from app.services import sync_scheduler
from tests._test_secrets import WRONG_PASSWORD


class _FakeConnector:
    """Stands in for a real DB connector's fetch_since - returns fixed rows
    once, then nothing, so tests can assert both the "new data" and
    "no new data" paths without a live database."""

    def __init__(self, rows_by_cursor: dict):
        self.rows_by_cursor = rows_by_cursor
        self.calls: list[tuple] = []

    def fetch_since(self, cursor_column, cursor_value, max_rows=500_000):
        self.calls.append((cursor_column, cursor_value, max_rows))
        rows = self.rows_by_cursor.get(cursor_value, [])
        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor


@pytest.fixture
def scoped_session_factory(test_engine, monkeypatch):
    """A real async_sessionmaker bound to the test's in-memory engine,
    installed as app.db.session.async_session_factory - the same pattern
    conftest.py's `client` fixture uses for TelemetryMiddleware. Returns the
    factory so tests can open their own session with it for fixture setup
    and assertions, sharing the same underlying StaticPool connection
    sync_scheduler's own sessions read/write through."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", factory)
    return factory


async def _make_schedule(session_factory, *, cursor_value=None) -> uuid.UUID:
    async with session_factory() as db:
        org = Org(name="Acme")
        db.add(org)
        await db.flush()
        user = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", hashed_password=WRONG_PASSWORD)
        db.add(user)
        await db.flush()
        integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
        db.add(integration)
        await db.flush()
        schedule = SyncSchedule(
            org_id=org.id, integration_id=integration.id, name="Line 1",
            column_map={"units_col": "units"}, cursor_column="ts", cursor_value=cursor_value,
            interval_seconds=300, created_by=user.id, desired_status="running",
        )
        db.add(schedule)
        await db.commit()
        return schedule.id


@pytest.mark.asyncio
async def test_run_sync_persists_tick_and_advances_cursor(scoped_session_factory, monkeypatch):
    schedule_id = await _make_schedule(scoped_session_factory)
    fake = _FakeConnector({None: [{"ts": "2026-08-01T00:00:00", "units": 5}, {"ts": "2026-08-01T00:01:00", "units": 3}]})
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)

    await sync_scheduler.run_sync(schedule_id)

    async with scoped_session_factory() as db:
        tick = (
            await db.execute(select(SyncTick).where(SyncTick.schedule_id == schedule_id))
        ).scalar_one()
        assert tick.row_count == 2
        assert tick.metrics["throughput"] == 8.0

        rows = (
            await db.execute(select(SyncRow).where(SyncRow.schedule_id == schedule_id))
        ).scalars().all()
        assert len(rows) == 2

        refreshed = (
            await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
        ).scalar_one()
        assert refreshed.cursor_value == "2026-08-01T00:01:00"
        assert refreshed.last_error is None


@pytest.mark.asyncio
async def test_run_sync_no_new_rows_is_a_noop(scoped_session_factory, monkeypatch):
    schedule_id = await _make_schedule(scoped_session_factory, cursor_value="2026-08-01T00:01:00")
    fake = _FakeConnector({})  # no rows for any cursor
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)

    await sync_scheduler.run_sync(schedule_id)

    async with scoped_session_factory() as db:
        tick_count = len((
            await db.execute(select(SyncTick).where(SyncTick.schedule_id == schedule_id))
        ).scalars().all())
        assert tick_count == 0
        refreshed = (
            await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
        ).scalar_one()
        assert refreshed.cursor_value == "2026-08-01T00:01:00"  # unchanged


@pytest.mark.asyncio
async def test_run_sync_failure_sets_last_error_and_keeps_cursor(scoped_session_factory, monkeypatch):
    schedule_id = await _make_schedule(scoped_session_factory, cursor_value="2026-08-01T00:01:00")

    class _BrokenConnector:
        def fetch_since(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: _BrokenConnector())

    await sync_scheduler.run_sync(schedule_id)  # must not raise

    async with scoped_session_factory() as db:
        refreshed = (
            await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
        ).scalar_one()
        assert "connection refused" in refreshed.last_error
        assert refreshed.cursor_value == "2026-08-01T00:01:00"


@pytest.mark.asyncio
async def test_row_buffer_is_trimmed_to_cap(scoped_session_factory, monkeypatch):
    schedule_id = await _make_schedule(scoped_session_factory)
    monkeypatch.setattr(sync_scheduler, "_ROW_BUFFER_CAP", 3)
    rows = [{"ts": f"2026-08-01T00:0{i}:00", "units": 1} for i in range(5)]
    fake = _FakeConnector({None: rows})
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)

    await sync_scheduler.run_sync(schedule_id)

    async with scoped_session_factory() as db:
        remaining = (
            await db.execute(select(SyncRow).where(SyncRow.schedule_id == schedule_id))
        ).scalars().all()
        assert len(remaining) == 3
        assert {r.row["ts"] for r in remaining} == {"2026-08-01T00:02:00", "2026-08-01T00:03:00", "2026-08-01T00:04:00"}


def test_add_and_remove_job_are_idempotent():
    sync_scheduler._scheduler = None  # ensure a clean module-level state for this test
    sync_scheduler.add_job(uuid.uuid4(), 300)  # scheduler not started - no-op, must not raise
    sync_scheduler.remove_job(uuid.uuid4())  # removing a job that was never added - must not raise
