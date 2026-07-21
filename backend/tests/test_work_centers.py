from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def test_create_and_list_work_center(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/work-centers", json={"code": "CNC", "name": "CNC Milling", "machine_capacity": 3},
        headers=headers,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["code"] == "CNC"
    assert body["machine_capacity"] == 3
    assert body["status"] == "active"

    listed = await client.get("/work-centers", headers=headers)
    assert len(listed.json()) == 1


async def test_duplicate_code_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/work-centers", json={"code": "CNC", "name": "CNC Milling"}, headers=headers)
    dup = await client.post("/work-centers", json={"code": "CNC", "name": "Other"}, headers=headers)
    assert dup.status_code == 409


async def test_retire_work_center(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = (
        await client.post("/work-centers", json={"code": "CNC", "name": "CNC Milling"}, headers=headers)
    ).json()
    res = await client.patch(f"/work-centers/{created['id']}", json={"status": "retired"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "retired"


async def test_rbac_blocks_non_owner_create(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import uuid as uuid_module
    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(
            org_id=uuid_module.UUID(org_id), email="member2@example.com",
            password_hash=hash_password(PASSWORD), role="member", is_active=True,
        ))
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post("/work-centers", json={"code": "X", "name": "X"}, headers=member)
    assert res.status_code == 403
    assert (await client.get("/work-centers", headers=member)).status_code == 200
