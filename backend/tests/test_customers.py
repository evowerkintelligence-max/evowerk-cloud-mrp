from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_create_and_list_customer(client):
    token = await _register_and_login(client)
    created = await _create_customer(client, token)
    assert created["name"] == "Northwind Ltd"
    assert created["is_active"] is True

    res = await client.get("/customers", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert [c["name"] for c in res.json()] == ["Northwind Ltd"]


async def test_duplicate_customer_name_is_rejected(client):
    token = await _register_and_login(client)
    await _create_customer(client, token)
    res = await client.post(
        "/customers", json={"name": "Northwind Ltd"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 409


async def test_deactivated_customer_is_hidden_from_the_picker(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)

    res = await client.post(f"/customers/{customer['id']}/deactivate", headers=headers)
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    active = await client.get("/customers?active_only=true", headers=headers)
    assert active.json() == []
    everyone = await client.get("/customers", headers=headers)
    assert len(everyone.json()) == 1


async def test_reactivate_restores_the_customer(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    customer = await _create_customer(client, token)
    await client.post(f"/customers/{customer['id']}/deactivate", headers=headers)

    res = await client.post(f"/customers/{customer['id']}/reactivate", headers=headers)
    assert res.status_code == 200
    assert res.json()["is_active"] is True


async def test_customers_are_org_scoped(client):
    token_a = await _register_and_login(client)
    await _create_customer(client, token_a)
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await client.get("/customers", headers={"Authorization": f"Bearer {token_b}"})
    assert res.json() == []


async def test_a_member_can_read_customers_but_not_create_them(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _create_customer(client, token)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "member2@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    create = await client.post("/customers", json={"name": "Contoso"}, headers=member_headers)
    assert create.status_code == 403
    listing = await client.get("/customers", headers=member_headers)
    assert listing.status_code == 200
