from __future__ import annotations

import os
import tempfile

from cryptography.fernet import Fernet

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-that-is-long-enough-for-hs256")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")  # pragma: allowlist secret
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
# Dataset uploads hit the real filesystem (only the DB is in-memory sqlite) -
# without this, every test run leaves orphaned files under ./data.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="evowerk-test-data-"))

import asyncio

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import background as background_module
from app.core import redis_client
from app.db import session as db_session_module
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
    device_token,
    downtime_tracking,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    machine,
    maintenance_schedule,
    operator,
    pace_tracking,
    procurement,
    rca_case,
    realtime,
    report_spec,
    routing,
    sales,
    sensor,
    shift_forecast,
    shift_schedule,
    simulation,
    staff,
    telemetry,
    tenant,
    user,
    work_center,
    work_order,
    workflow,
)
from app.models.tenant import Org

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


async def drain_background_tasks() -> None:
    """Routes that use app.core.background.fire_and_forget (password reset,
    verification emails, PO-submit notifications) don't await the scheduled
    coroutine, so the response can return before it finishes - tests
    asserting its side effect need to wait for it explicitly instead of
    relying on it having finished by request-return."""
    await asyncio.gather(*background_module._background_tasks)


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


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

    app.dependency_overrides[db_session_module.get_db] = _get_db_override
    app.dependency_overrides[redis_client.get_redis_pool] = _get_redis_override
    # TelemetryMiddleware opens its own session outside the DI chain, so it needs
    # its own patch of the factory to land in the test DB instead of the real one.
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await fake_redis.aclose()


# Modules that test the entitlement-default behavior itself and must see a
# freshly-registered org's real (empty) default, not the blanket grant below.
_ENTITLEMENT_DEFAULT_TEST_MODULES = {"test_feature_gating", "test_org_features_migration"}

# What an org created during a test is entitled to. A static snapshot on
# purpose - see _default_full_entitlements below for why this is not
# app.core.features.PREMIUM_FEATURES. Adding a feature key to the registry
# means adding it here too, or that feature's routes will 403 in tests.
_GRANTED_IN_TESTS = [
    "boms", "procurement", "sales", "production", "lots", "warehouse", "planning",
    "plant_summary", "oee", "pace_tracking", "downtime", "downtime_tracking",
    "throughput", "scrap", "shifts", "spc", "kpis", "kpi_dashboards",
    "reports", "data_chat", "automl", "predictive", "simulation",
    "rca_cases", "mfg_forecast", "computer_vision",
]


@pytest.fixture(autouse=True)
def _default_full_entitlements(request):
    # ponytail: pre-existing tests (60+ files) predate per-org feature gating
    # and assume a fresh org can hit every router. Rather than hand-editing
    # every test file's local _register_and_login helper, grant every org
    # created during a test the keys below, here in the one shared fixture
    # module. Upgrade path: if a test needs to exercise a specific
    # entitlement gap, have it override org.enabled_features directly (as
    # test_feature_gating.py already does).
    #
    # The list is a STATIC snapshot, deliberately not derived from
    # app.core.features. Deriving it meant a key added to the registry
    # granted itself here too, so no test could ever notice the entitlement
    # dimension and the `sales` key shipped 403-ing for every existing
    # customer. Now a new key gets no access until it is added here, which is
    # a moment to ask who is actually entitled to it. That question is
    # enforced for real by tests/test_feature_migration_coverage.py; this
    # snapshot only stops the suite from silently answering it for you.
    if request.module.__name__.rsplit(".", 1)[-1] in _ENTITLEMENT_DEFAULT_TEST_MODULES:
        yield
        return

    def _grant(mapper, connection, target):
        if not target.enabled_features:
            target.enabled_features = list(_GRANTED_IN_TESTS)

    event.listen(Org, "before_insert", _grant)
    try:
        yield
    finally:
        event.remove(Org, "before_insert", _grant)
