from __future__ import annotations

import pyotp
import pytest

pytestmark = pytest.mark.asyncio


async def _register(client, email="alice@example.com", org_name="Acme"):
    res = await client.post(
        "/auth/register",
        json={"org_name": org_name, "email": email, "password": "correct-horse-battery-staple"},
    )
    assert res.status_code == 201
    return res.json()


async def test_register_login_refresh_logout(client):
    await _register(client)

    login = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "correct-horse-battery-staple"}
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["mfa_required"] is False

    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "Acme"

    refreshed = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # old refresh token is now revoked (single-use rotation)
    reused = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401

    logout = await client.post("/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    assert logout.status_code == 204

    after_logout = await client.post("/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert after_logout.status_code == 401


async def test_login_wrong_password_rejected(client):
    await _register(client, email="bob@example.com")
    res = await client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong"})
    assert res.status_code == 401


async def test_mfa_enroll_and_login(client):
    await _register(client, email="carol@example.com")
    login = await client.post(
        "/auth/login", json={"email": "carol@example.com", "password": "correct-horse-battery-staple"}
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    enroll = await client.post("/auth/mfa/enroll", headers=headers)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]

    code = pyotp.TOTP(secret).now()
    verify = await client.post("/auth/mfa/verify", json={"code": code}, headers=headers)
    assert verify.status_code == 204

    # Login now requires MFA
    login_no_code = await client.post(
        "/auth/login", json={"email": "carol@example.com", "password": "correct-horse-battery-staple"}
    )
    assert login_no_code.status_code == 200
    assert login_no_code.json()["mfa_required"] is True

    login_with_code = await client.post(
        "/auth/login",
        json={
            "email": "carol@example.com",
            "password": "correct-horse-battery-staple",
            "totp_code": pyotp.TOTP(secret).now(),
        },
    )
    assert login_with_code.status_code == 200
    assert login_with_code.json()["access_token"]

    login_wrong_code = await client.post(
        "/auth/login",
        json={
            "email": "carol@example.com",
            "password": "correct-horse-battery-staple",
            "totp_code": "000000",
        },
    )
    assert login_wrong_code.status_code == 401


async def test_cross_tenant_isolation(client):
    await _register(client, email="dave@example.com", org_name="OrgA")
    await _register(client, email="erin@example.com", org_name="OrgB")

    login_a = await client.post(
        "/auth/login", json={"email": "dave@example.com", "password": "correct-horse-battery-staple"}
    )
    login_b = await client.post(
        "/auth/login", json={"email": "erin@example.com", "password": "correct-horse-battery-staple"}
    )

    token_a = login_a.json()["access_token"]
    token_b = login_b.json()["access_token"]

    org_a = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token_a}"})
    org_b = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token_b}"})

    assert org_a.json()["name"] == "OrgA"
    assert org_b.json()["name"] == "OrgB"
    assert org_a.json()["id"] != org_b.json()["id"]
