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
    return login.json()["access_token"], reg.json()["user_id"]


async def _register_login_and_make_staff(client, email="staff@evowerk.example", org_name="EvoWerk Staff"):
    token, user_id = await _register_and_login(client, email=email, org_name=org_name)
    async with db_session.async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one()
        user.is_staff = True
        await db.commit()
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_non_staff_cannot_create_customer(client):
    token, _user_id = await _register_and_login(client)
    res = await client.post(
        "/admin/customers", headers=_h(token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 403


async def test_staff_creates_customer_with_features(client):
    staff_token = await _register_login_and_make_staff(client)

    res = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee", "downtime"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["org_name"] == "New Co"
    assert body["owner_email"] == "owner@newco.example"
    assert sorted(body["enabled_features"]) == ["downtime", "oee"]

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(body["org_id"])))).scalar_one()
        owner = (await db.execute(select(User).where(User.email == "owner@newco.example"))).scalar_one()
    assert sorted(org.enabled_features) == ["downtime", "oee"]
    assert owner.role == "owner"
    assert owner.org_id == org.id


async def test_staff_lists_and_edits_customer_features(client):
    staff_token = await _register_login_and_make_staff(client)
    create = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    org_id = create.json()["org_id"]

    listed = await client.get("/admin/customers", headers=_h(staff_token))
    assert listed.status_code == 200, listed.text
    assert any(c["org_id"] == org_id and c["enabled_features"] == ["oee"] for c in listed.json())

    updated = await client.patch(
        f"/admin/customers/{org_id}", headers=_h(staff_token),
        json={"enabled_features": ["oee", "automl"]},
    )
    assert updated.status_code == 200, updated.text
    assert sorted(updated.json()["enabled_features"]) == ["automl", "oee"]

    async with db_session.async_session_factory() as db:
        org = (await db.execute(select(Org).where(Org.id == uuid.UUID(org_id)))).scalar_one()
    assert sorted(org.enabled_features) == ["automl", "oee"]


async def test_create_customer_rejects_unknown_feature_key(client):
    staff_token = await _register_login_and_make_staff(client)
    res = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["not_a_real_feature"]},
    )
    assert res.status_code == 422, res.text


async def test_update_customer_features_rejects_unknown_feature_key(client):
    staff_token = await _register_login_and_make_staff(client)
    create = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    org_id = create.json()["org_id"]

    res = await client.patch(
        f"/admin/customers/{org_id}", headers=_h(staff_token),
        json={"enabled_features": ["not_a_real_feature"]},
    )
    assert res.status_code == 422, res.text


async def test_create_customer_duplicate_owner_email_409s(client):
    staff_token = await _register_login_and_make_staff(client)
    first = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert first.status_code == 201, first.text

    dupe = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "Other Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert dupe.status_code == 409, dupe.text


async def test_patch_unknown_customer_404s(client):
    staff_token = await _register_login_and_make_staff(client)
    res = await client.patch(
        "/admin/customers/00000000-0000-0000-0000-000000000000", headers=_h(staff_token),
        json={"enabled_features": ["oee"]},
    )
    assert res.status_code == 404
