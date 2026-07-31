from __future__ import annotations

import pyotp
import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


async def _register(client, email="alice@example.com", org_name="Acme", password=PASSWORD):
    res = await client.post(
        "/auth/register",
        json={"org_name": org_name, "email": email, "password": password},
    )
    assert res.status_code == 201
    return res.json()


async def test_register_login_refresh_logout(client):
    await _register(client)

    login = await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
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


async def test_delete_account_scrubs_email_and_revokes_access(client):
    await _register(client)
    login = await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    tokens = login.json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    wrong_password = await client.request("DELETE", "/auth/me", json={"password": "wrong"}, headers=auth)
    assert wrong_password.status_code == 401

    deleted = await client.request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=auth)
    assert deleted.status_code == 204

    # the deleted account can no longer log in with its old email/password...
    relogin = await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert relogin.status_code == 401

    # ...and the old refresh token no longer works.
    refreshed = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 401

    # the email is now free for a new registration (it's been anonymized, not left dangling).
    reregistered = await _register(client)
    assert reregistered["email"] == "alice@example.com"


async def test_register_rejects_weak_password(client):
    res = await client.post(
        "/auth/register",
        json={"org_name": "WeakCo", "email": "weak@example.com", "password": "short1"},
    )
    assert res.status_code == 422

    res_no_digit = await client.post(
        "/auth/register",
        json={"org_name": "WeakCo", "email": "weak2@example.com", "password": "all-letters-no-digits"},
    )
    assert res_no_digit.status_code == 422


async def test_register_duplicate_email_rejected(client):
    await _register(client, email="dupe@example.com")
    res = await client.post(
        "/auth/register",
        json={"org_name": "Other", "email": "dupe@example.com", "password": PASSWORD},
    )
    assert res.status_code == 409


async def test_login_wrong_password_rejected(client):
    await _register(client, email="bob@example.com")
    res = await client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong"})
    assert res.status_code == 401


async def test_login_nonexistent_user_rejected(client):
    res = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever-guess-1"}
    )
    assert res.status_code == 401


async def test_account_lockout_after_repeated_failures(client):
    await _register(client, email="lockout@example.com")

    for _ in range(5):
        res = await client.post(
            "/auth/login", json={"email": "lockout@example.com", "password": "wrong-password"}
        )
        assert res.status_code == 401

    # 6th attempt (even with the *correct* password) is locked out
    locked = await client.post(
        "/auth/login", json={"email": "lockout@example.com", "password": PASSWORD}
    )
    assert locked.status_code == 429


async def test_mfa_enroll_verify_login_and_lockout(client):
    await _register(client, email="carol@example.com")
    login = await client.post("/auth/login", json={"email": "carol@example.com", "password": PASSWORD})
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    enroll = await client.post("/auth/mfa/enroll", json={}, headers=headers)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]

    code = pyotp.TOTP(secret).now()
    verify = await client.post("/auth/mfa/verify", json={"code": code}, headers=headers)
    assert verify.status_code == 204

    # Login now requires MFA
    login_no_code = await client.post(
        "/auth/login", json={"email": "carol@example.com", "password": PASSWORD}
    )
    assert login_no_code.status_code == 200
    assert login_no_code.json()["mfa_required"] is True

    login_with_code = await client.post(
        "/auth/login",
        json={"email": "carol@example.com", "password": PASSWORD, "totp_code": pyotp.TOTP(secret).now()},
    )
    assert login_with_code.status_code == 200
    assert login_with_code.json()["access_token"]

    login_wrong_code = await client.post(
        "/auth/login",
        json={"email": "carol@example.com", "password": PASSWORD, "totp_code": "000000"},
    )
    assert login_wrong_code.status_code == 401

    # Re-enrolling on an account that already has MFA on requires password confirmation
    re_enroll_no_password = await client.post("/auth/mfa/enroll", json={}, headers=headers)
    assert re_enroll_no_password.status_code == 403

    re_enroll_with_password = await client.post(
        "/auth/mfa/enroll", json={"password": PASSWORD}, headers=headers
    )
    assert re_enroll_with_password.status_code == 200


async def test_mfa_enroll_alone_does_not_disable_existing_mfa(client):
    """Regression test: calling /mfa/enroll must not turn off MFA until the new
    secret is actually verified - otherwise a leaked access token could be used to
    silently disable MFA on an account without ever proving the new device."""
    await _register(client, email="dave2@example.com")
    login = await client.post("/auth/login", json={"email": "dave2@example.com", "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    enroll1 = await client.post("/auth/mfa/enroll", json={}, headers=headers)
    secret1 = enroll1.json()["secret"]
    await client.post(
        "/auth/mfa/verify", json={"code": pyotp.TOTP(secret1).now()}, headers=headers
    )

    # Fresh login, MFA should still be required
    login2 = await client.post("/auth/login", json={"email": "dave2@example.com", "password": PASSWORD})
    assert login2.status_code == 200
    assert login2.json()["mfa_required"] is True

    # Attempt to re-enroll without password confirmation - must be rejected, and must
    # NOT have disabled the existing MFA secret as a side effect.
    bad_reenroll = await client.post("/auth/mfa/enroll", json={}, headers=headers)
    assert bad_reenroll.status_code == 403

    login3 = await client.post("/auth/login", json={"email": "dave2@example.com", "password": PASSWORD})
    assert login3.status_code == 200
    assert login3.json()["mfa_required"] is True, "MFA must still be enforced after a rejected re-enroll"


async def test_cross_tenant_isolation(client):
    await _register(client, email="frank@example.com", org_name="OrgA")
    await _register(client, email="grace@example.com", org_name="OrgB")

    login_a = await client.post("/auth/login", json={"email": "frank@example.com", "password": PASSWORD})
    login_b = await client.post("/auth/login", json={"email": "grace@example.com", "password": PASSWORD})

    token_a = login_a.json()["access_token"]
    token_b = login_b.json()["access_token"]

    org_a = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token_a}"})
    org_b = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token_b}"})

    assert org_a.json()["name"] == "OrgA"
    assert org_b.json()["name"] == "OrgB"
    assert org_a.json()["id"] != org_b.json()["id"]
