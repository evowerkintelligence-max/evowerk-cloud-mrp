# backend/tests/test_downtime_tracking.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "dt@example.com", org_name: str = "DtOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_downtime_incident_model_roundtrip(client, test_engine):
    from app.models.downtime_tracking import DowntimeIncident

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        incident = DowntimeIncident(
            org_id=org_id, started_by=user_id, machine="Line 1", reason="breakdown", shift="A"
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        assert incident.id is not None
        assert incident.started_at is not None
        assert incident.ended_at is None
