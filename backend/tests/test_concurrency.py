"""Real-concurrency tests for the race guards (guarded UPDATEs, non-negative
guards, SAVEPOINT create/allocation retries).

These are gated on CONCURRENCY_DATABASE_URL because the default test DB is a
single-connection in-memory SQLite (StaticPool) that can't actually interleave
transactions - so the whole suite's race guards are otherwise only exercised on
their happy paths. Here we point at a real Postgres, give the app a real
connection pool, and fire genuinely concurrent requests via asyncio.gather; each
request gets its own connection, so the DB-level races really happen.

Run locally with e.g.:
    CONCURRENCY_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/evowerk_cloud_mrp \
        pytest tests/test_concurrency.py
CI runs them in the dedicated `concurrency` job (see .github/workflows/ci.yml).
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest
import pytest_asyncio

CONCURRENCY_DB_URL = os.getenv("CONCURRENCY_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not CONCURRENCY_DB_URL,
        reason="Set CONCURRENCY_DATABASE_URL to a Postgres async URL to run concurrency tests",
    ),
]

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


@pytest_asyncio.fixture
async def pg_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.base import Base
    from app.models import (  # noqa: F401 - register models on Base.metadata
        audit,
        bom,
        forecast,
        inventory,
        lot,
        procurement,
        tenant,
        user,
    )

    # A real pool (not StaticPool): concurrent requests each take their own
    # connection, which is what lets the races actually occur.
    engine = create_async_engine(CONCURRENCY_DB_URL, pool_size=20, max_overflow=0)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_engine, monkeypatch):
    import fakeredis.aioredis as fakeredis
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core import redis_client
    from app.db import session as db_session
    from app.main import app

    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            yield session

    fake_redis = fakeredis.FakeRedis(decode_responses=True)

    async def _get_redis_override():
        return fake_redis

    app.dependency_overrides[db_session.get_db] = _get_db_override
    app.dependency_overrides[redis_client.get_redis] = _get_redis_override
    # TelemetryMiddleware opens its own session outside the DI chain via the
    # module-level async_session_factory, which is bound to the default engine
    # created once at import time in whatever event loop was running then.
    # Left unpatched, every request leaks a connection from that stale pool -
    # fine for the first test in the file, but pytest-asyncio hands each test
    # function a fresh event loop, so every test after the first hits asyncpg
    # connections still attached to a closed loop ("Future ... attached to a
    # different loop"). Point it at this test's own pg-backed factory instead.
    monkeypatch.setattr(db_session, "async_session_factory", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await fake_redis.aclose()


async def _auth(client, email="owner@example.com"):
    await client.post(
        "/auth/register", json={"org_name": "Acme", "email": email, "password": PASSWORD}
    )
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _item(client, headers, sku="ROD", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": "Rod", "item_type": "raw_material", "is_lot_tracked": is_lot_tracked},
        headers=headers,
    )
    return res.json()


async def _supplier(client, headers):
    return (await client.post("/suppliers", json={"name": "S"}, headers=headers)).json()


async def _receive(client, headers, item, supplier, qty, lot_number=None):
    po = (await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)).json()
    lines = await client.patch(
        f"/purchase-orders/{po['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
    body = {"po_line_id": line_id, "quantity": str(qty)}
    if lot_number:
        body["lot_number"] = lot_number
    await client.post(f"/purchase-orders/{po['id']}/receive", json=[body], headers=headers)


async def test_concurrent_inventory_consume_never_oversells(pg_client):
    headers = await _auth(pg_client)
    item = await _item(pg_client, headers)
    supplier = await _supplier(pg_client, headers)
    await _receive(pg_client, headers, item, supplier, 100)

    # 15 concurrent consumes of 10 from 100 on hand: exactly 10 can succeed.
    results = await asyncio.gather(
        *[
            pg_client.post(f"/inventory/{item['id']}/consume", json={"quantity": "10"}, headers=headers)
            for _ in range(15)
        ]
    )
    ok = [r for r in results if r.status_code == 200]
    conflict = [r for r in results if r.status_code == 409]
    assert len(ok) == 10, [r.status_code for r in results]
    assert len(conflict) == 5
    # Never oversold, never negative.
    detail = await pg_client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(detail.json()["quantity_on_hand"]) == Decimal("0")


async def test_concurrent_inventory_adjust_no_lost_updates(pg_client):
    headers = await _auth(pg_client)
    item = await _item(pg_client, headers)

    # 12 concurrent +5 adjustments on an item with NO balance row yet: they all
    # race to create the balance (SAVEPOINT insert), then accumulate. The guard's
    # contract is that no successful increment is ever lost and on-hand can't be
    # corrupted; under a heavy burst of first-creates a loser may exhaust its
    # retries and get a clean, retry-able 409 (same contract as po_number), so we
    # assert the invariant, not that every request wins the create race.
    results = await asyncio.gather(
        *[
            pg_client.post(f"/inventory/{item['id']}/adjust", json={"quantity": "5"}, headers=headers)
            for _ in range(12)
        ]
    )
    statuses = [r.status_code for r in results]
    assert all(s in (200, 409) for s in statuses), statuses
    ok = [r for r in results if r.status_code == 200]
    assert ok, "expected at least one adjustment to succeed"
    detail = await pg_client.get(f"/inventory/{item['id']}", headers=headers)
    # Final on-hand is exactly 5 per success: no increment lost, nothing double
    # counted, no phantom value from a failed create.
    assert Decimal(detail.json()["quantity_on_hand"]) == Decimal("5") * len(ok)


async def test_concurrent_lot_consume_never_oversells(pg_client):
    headers = await _auth(pg_client)
    item = await _item(pg_client, headers, is_lot_tracked=True)
    supplier = await _supplier(pg_client, headers)
    await _receive(pg_client, headers, item, supplier, 100, lot_number="LOT-1")
    lot_id = (await pg_client.get("/lots", headers=headers)).json()[0]["id"]

    results = await asyncio.gather(
        *[
            pg_client.post(f"/lots/{lot_id}/consume", json={"quantity": "10"}, headers=headers)
            for _ in range(15)
        ]
    )
    ok = [r for r in results if r.status_code == 200]
    assert len(ok) == 10, [r.status_code for r in results]
    lot = await pg_client.get(f"/lots/{lot_id}", headers=headers)
    assert Decimal(lot.json()["quantity_remaining"]) == Decimal("0")


async def test_concurrent_po_creation_never_duplicates_numbers(pg_client):
    headers = await _auth(pg_client)
    supplier = await _supplier(pg_client, headers)

    # Concurrent creates contend on the po_number max+1 allocation. The guard's
    # contract: never a duplicate po_number, never an unhandled 500. Under heavy
    # contention a request may exhaust its retries and get a clean 409 - that's
    # acceptable (documented "please retry"), so we assert the invariant, not
    # that every request succeeds.
    results = await asyncio.gather(
        *[
            pg_client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
            for _ in range(10)
        ]
    )
    statuses = [r.status_code for r in results]
    assert all(s in (201, 409) for s in statuses), statuses
    numbers = [r.json()["po_number"] for r in results if r.status_code == 201]
    assert len(numbers) == len(set(numbers)), f"duplicate po_numbers: {sorted(numbers)}"
    assert numbers, "expected at least one PO to be created"
