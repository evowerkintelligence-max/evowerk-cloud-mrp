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

import fakeredis.aioredis as fakeredis
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import redis_client
from app.db import session as db_session
from app.db.base import Base
from app.main import app
from app.models import (  # noqa: F401 - register models on the metadata
    alert,
    audit,
    bom,
    dashboard,
    dataset,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    maintenance_schedule,
    procurement,
    rca_case,
    realtime,
    report_spec,
    shift_schedule,
    simulation,
    telemetry,
    tenant,
    user,
    workflow,
)

engine = create_async_engine(os.environ["DATABASE_URL"])
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def _get_db_override():
    async with session_factory() as session:
        yield session


fake_redis = fakeredis.FakeRedis(decode_responses=True)


async def _get_redis_override():
    return fake_redis


app.dependency_overrides[db_session.get_db] = _get_db_override
app.dependency_overrides[redis_client.get_redis_pool] = _get_redis_override


async def _init_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(_init_tables())
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), log_level="warning")
