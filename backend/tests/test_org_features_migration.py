from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["org_id"], reg.json()["user_id"]


async def test_new_org_and_user_default_to_no_entitlements(client):
    _token, org_id, user_id = await _register_and_login(client)

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
        user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one()

    assert org.enabled_features == []
    assert user.is_staff is False
