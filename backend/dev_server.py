"""Local dev server: runs the real app on a SQLite file + fake Redis, so you can
click through the UI without Postgres/Redis/Docker. Mirrors tests/conftest.py.
Not for production. Delete freely."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
os.environ.setdefault("JWT_SECRET_KEY", "dev-secret-that-is-long-enough-for-hs256-0123456789")
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import asyncio
import socket
import sys

import fakeredis.aioredis as fakeredis
import uvicorn
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import redis_client
from app.db import session as db_session
from app.db.base import Base
from app.main import app
from app.services import realtime as realtime_service
from app.services import realtime_cache
from app.models import (  # noqa: F401 - register models on the metadata
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    capture_event,
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

# timeout: how long a connection waits on a locked dev.db instead of raising
# "database is locked" immediately - this is a real file on disk shared by
# every request plus any one-off script you point at it (a seed script, a
# manual DB check) while the server is up, unlike the in-memory single-connection
# sqlite tests/conftest.py uses. WAL lets those readers/writers overlap instead
# of contending for the same lock in the first place.
engine = create_async_engine(os.environ["DATABASE_URL"], connect_args={"timeout": 30})
session_factory = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _enable_wal(dbapi_conn, _record) -> None:
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA busy_timeout=30000")


async def _get_db_override():
    async with session_factory() as session:
        yield session


fake_redis = fakeredis.FakeRedis(decode_responses=True)


async def _get_redis_override():
    return fake_redis


app.dependency_overrides[db_session.get_db] = _get_db_override
app.dependency_overrides[redis_client.get_redis_pool] = _get_redis_override


async def _noop(*_args, **_kwargs) -> None:
    return None


# app.main's lifespan hard-requires a live Kafka/Redpanda broker (realtime
# streaming), which this script otherwise has no fake for (unlike DB/Redis
# above) - no-op it so the app can actually start without Docker.
realtime_service.resume_all_streams = _noop
realtime_service.stop_all_runners = _noop
realtime_cache.start_cache_consumer = _noop
realtime_cache.stop_cache_consumer = _noop


async def _init_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _already_running(host: str, port: int) -> bool:
    """True if something is already accepting connections on host:port - almost
    always a previous copy of this same script left over from an earlier run
    (restarted from an editor, a crashed shell, etc.), since nothing else binds
    127.0.0.1 on this port in the dev workflow. Exiting cleanly in that case
    instead of letting uvicorn's bind fail beats reproducing that every time
    you re-run the script."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) == 0


if __name__ == "__main__":
    host, port = "127.0.0.1", int(os.environ.get("PORT", 8000))
    if _already_running(host, port):
        print(f"Dev server already running on {host}:{port} - nothing to do.")
        sys.exit(0)
    asyncio.run(_init_tables())
    uvicorn.run(app, host=host, port=port, log_level="warning")
