from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest
from sqlalchemy import select

from app.api.routes import admin as admin_routes
from app.core.security import hash_password
from app.db import session as db_session
from app.models.staff import StaffAccount
from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], reg.json()["user_id"]


async def _make_staff_token(client, employee_id="staff1", password=TEST_PASSWORD):
    async with db_session.async_session_factory() as db:
        db.add(StaffAccount(employee_id=employee_id, hashed_password=hash_password(password)))
        await db.commit()
    login = await client.post("/staff/auth/login", json={"employee_id": employee_id, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_no_token_cannot_create_customer(client):
    res = await client.post(
        "/admin/customers",
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 401


async def test_customer_token_cannot_create_customer(client):
    customer_token, _user_id = await _register_and_login(client)
    res = await client.post(
        "/admin/customers", headers=_h(customer_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 401


async def test_staff_creates_customer_with_features(client):
    staff_token = await _make_staff_token(client)

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
    staff_token = await _make_staff_token(client)
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
    staff_token = await _make_staff_token(client)
    res = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["not_a_real_feature"]},
    )
    assert res.status_code == 422, res.text


async def test_update_customer_features_rejects_unknown_feature_key(client):
    staff_token = await _make_staff_token(client)
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
    staff_token = await _make_staff_token(client)
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


async def test_create_customer_when_random_password_has_no_digit(client, monkeypatch):
    # secrets.token_urlsafe returns a digit-free string ~1 draw in 215, which
    # UserCreate's password policy rejects - forced here so the endpoint can't
    # go back to depending on that coin flip (it used to 500).
    monkeypatch.setattr(admin_routes.secrets, "token_urlsafe", lambda n: "a" * n)
    staff_token = await _make_staff_token(client)
    res = await client.post(
        "/admin/customers", headers=_h(staff_token),
        json={"org_name": "New Co", "owner_email": "owner@newco.example", "enabled_features": ["oee"]},
    )
    assert res.status_code == 201, res.text


async def test_patch_unknown_customer_404s(client):
    staff_token = await _make_staff_token(client)
    res = await client.patch(
        "/admin/customers/00000000-0000-0000-0000-000000000000", headers=_h(staff_token),
        json={"enabled_features": ["oee"]},
    )
    assert res.status_code == 404
