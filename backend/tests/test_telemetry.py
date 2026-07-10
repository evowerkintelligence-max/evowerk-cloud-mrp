from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.telemetry import UsageEvent

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def test_records_authenticated_request_and_skips_health(client, test_engine):
    await client.get("/health")

    reg = await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD},
    )
    token = (
        await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    ).json()["access_token"]
    await client.get("/items", headers={"Authorization": f"Bearer {token}"})

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        events = (await db.execute(select(UsageEvent))).scalars().all()

    paths = [e.path for e in events]
    assert "/health" not in paths
    assert "/items" in paths
    items_event = next(e for e in events if e.path == "/items")
    assert items_event.status_code == 200
    assert items_event.org_id == uuid.UUID(reg.json()["org_id"])
