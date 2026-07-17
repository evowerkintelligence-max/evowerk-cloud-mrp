from __future__ import annotations

import os
import tempfile

from cryptography.fernet import Fernet

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-that-is-long-enough-for-hs256")
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
# Dataset uploads hit the real filesystem (only the DB is in-memory sqlite) -
# without this, every test run leaves orphaned files under ./data.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="evowerk-test-data-"))

import fakeredis.aioredis as fakeredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import redis_client
from app.db import session as db_session
from app.db.base import Base
from app.main import app
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    dashboard,
    dataset,
    downtime_tracking,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    maintenance_schedule,
    pace_tracking,
    procurement,
    rca_case,
    realtime,
    report_spec,
    scenario,
    shift_schedule,
    telemetry,
    tenant,
    user,
    workflow,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            yield session

    # In-memory fake Redis per test, so rate-limit counters don't leak between
    # tests (and no real Redis instance is needed to run the suite/CI).
    fake_redis = fakeredis.FakeRedis(decode_responses=True)

    async def _get_redis_override():
        return fake_redis

    app.dependency_overrides[db_session.get_db] = _get_db_override
    app.dependency_overrides[redis_client.get_redis_pool] = _get_redis_override
    # TelemetryMiddleware opens its own session outside the DI chain, so it needs
    # its own patch of the factory to land in the test DB instead of the real one.
    monkeypatch.setattr(db_session, "async_session_factory", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await fake_redis.aclose()
