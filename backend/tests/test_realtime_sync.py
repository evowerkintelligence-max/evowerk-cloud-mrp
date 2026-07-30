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
