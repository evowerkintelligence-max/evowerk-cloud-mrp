from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest
from sqlalchemy import select

from app.models.device_token import DeviceToken

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


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


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_register_device_token(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token)
    )
    assert res.status_code == 204


async def test_register_device_token_upserts_on_repeat(client):
    token = await _register_and_login(client)
    payload = {"expo_push_token": "ExponentPushToken[abc123]"}
    first = await client.post("/device-tokens", json=payload, headers=_h(token))
    second = await client.post("/device-tokens", json=payload, headers=_h(token))
    assert first.status_code == 204
    assert second.status_code == 204


async def test_delete_device_token(client):
    token = await _register_and_login(client)
    await client.post("/device-tokens", json={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token))
    res = await client.delete(
        "/device-tokens", params={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token)
    )
    assert res.status_code == 204


async def test_delete_device_token_from_other_user_is_noop(client, db_session):
    token_a = await _register_and_login(client, email="owner-a@example.com", org_name="Acme A")
    token_b = await _register_and_login(client, email="owner-b@example.com", org_name="Acme B")
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[shared]"}, headers=_h(token_a)
    )

    res = await client.delete(
        "/device-tokens", params={"expo_push_token": "ExponentPushToken[shared]"}, headers=_h(token_b)
    )
    assert res.status_code == 204

    remaining = (
        await db_session.execute(
            select(DeviceToken).where(DeviceToken.expo_push_token == "ExponentPushToken[shared]")
        )
    ).scalar_one_or_none()
    assert remaining is not None
