from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD, WRONG_PASSWORD

import pytest

from app.core.security import hash_password
from app.db import session as db_session
from app.models.staff import StaffAccount

pytestmark = pytest.mark.asyncio


async def _seed_staff(employee_id="zeeshan", password=TEST_PASSWORD):
    async with db_session.async_session_factory() as db:
        db.add(StaffAccount(employee_id=employee_id, hashed_password=hash_password(password)))
        await db.commit()


async def test_staff_login_succeeds_with_correct_credentials(client):
    await _seed_staff()
    res = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_staff_login_401s_on_wrong_password(client):
    await _seed_staff()
    res = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": WRONG_PASSWORD})
    assert res.status_code == 401


async def test_staff_login_401s_on_unknown_employee_id(client):
    res = await client.post("/staff/auth/login", json={"employee_id": "nobody", "password": WRONG_PASSWORD})
    assert res.status_code == 401


async def test_staff_token_rejected_by_customer_only_route(client):
    await _seed_staff()
    login = await client.post("/staff/auth/login", json={"employee_id": "zeeshan", "password": TEST_PASSWORD})
    staff_token = login.json()["access_token"]

    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {staff_token}"})
    assert res.status_code == 401
