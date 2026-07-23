from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.device_token import DeviceToken

pytestmark = pytest.mark.asyncio


async def test_device_token_round_trips(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db_session.add(DeviceToken(org_id=org_id, user_id=user_id, expo_push_token="ExponentPushToken[abc123]"))
    await db_session.commit()

    row = (
        await db_session.execute(select(DeviceToken).where(DeviceToken.org_id == org_id))
    ).scalar_one()
    assert row.user_id == user_id
    assert row.expo_push_token == "ExponentPushToken[abc123]"
    assert row.created_at is not None
