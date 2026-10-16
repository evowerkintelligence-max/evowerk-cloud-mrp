from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

from unittest.mock import AsyncMock, patch

import pyotp
import pytest

pytestmark = pytest.mark.asyncio


async def _register_with_mfa(client, email):
    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": email, "password": TEST_PASSWORD},
    )
    login = await client.post(
        "/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    enroll = await client.post("/auth/mfa/enroll", json={}, headers=headers)
    secret = enroll.json()["secret"]
    verify = await client.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}, headers=headers)
    assert verify.status_code == 204
    return secret


async def _google_state(client) -> str:
    """Drives the real /authorize route (with google_client mocked) so the
    resulting state value matches the httponly cookie already sitting in the
    client's cookie jar - callback tests need both to pass _verify_state()."""
    with patch("app.api.routes.oauth.google_client") as mock_client:
        mock_client.get_authorization_url = AsyncMock(return_value="https://accounts.google.com/o/oauth2/auth")
        await client.get("/auth/google/authorize", follow_redirects=False)
        return mock_client.get_authorization_url.call_args.kwargs["state"]


async def test_google_authorize_redirects_to_google(client):
    with patch("app.api.routes.oauth.google_client") as mock_client:
        mock_client.get_authorization_url = AsyncMock(return_value="https://accounts.google.com/o/oauth2/auth?...")
        res = await client.get("/auth/google/authorize", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "accounts.google.com" in res.headers["location"]


async def test_google_callback_logs_in_existing_account_when_email_verified(client):
    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "provisioned@example.com", "password": TEST_PASSWORD},
    )
    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-1", "provisioned@example.com"))

        res = await client.get(f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)

    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert "access_token=" in location
    assert "refresh_token=" in location


async def test_google_callback_rejects_mismatched_state(client):
    from fastapi import status

    await _google_state(client)  # sets a real state cookie, then we ignore it

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-csrf", "provisioned@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=attacker-supplied-state", follow_redirects=False)

    assert res.status_code == status.HTTP_400_BAD_REQUEST


async def test_google_callback_rejects_unknown_email(client):
    from fastapi import status

    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-unknown", "nobody@example.com"))

        res = await client.get(f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)

    assert res.status_code == status.HTTP_403_FORBIDDEN


async def test_google_callback_rejects_auto_link_when_email_not_verified(client):
    from fastapi import status

    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "existing@example.com", "password": TEST_PASSWORD},
    )
    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=False)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-2", "existing@example.com"))

        res = await client.get(f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)

    # Must NOT silently log in as the existing password account. Because
    # `email` is a unique column, "don't auto-link" can't mean "create a
    # second User row with the same email" either - it must mean "reject
    # the OAuth login" (no tokens issued) rather than trusting an
    # unverified provider email that happens to match an existing account.
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # The existing account itself is untouched - its real password still works.
    login = await client.post(
        "/auth/login", json={"email": "existing@example.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200


async def test_google_callback_for_mfa_enabled_account_issues_pending_token_not_tokens(client):
    await _register_with_mfa(client, "mfa-oauth@example.com")
    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-mfa", "mfa-oauth@example.com"))

        res = await client.get(f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)

    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert "mfa_pending_token=" in location
    assert "access_token=" not in location


async def test_oauth_mfa_verify_with_correct_code_issues_tokens(client):
    secret = await _register_with_mfa(client, "mfa-oauth-verify@example.com")
    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-mfa-2", "mfa-oauth-verify@example.com"))

        callback_res = await client.get(
            f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
        )

    location = callback_res.headers["location"]
    pending_token = location.split("mfa_pending_token=")[1]

    res = await client.post(
        "/auth/oauth/mfa-verify", json={"pending_token": pending_token, "code": pyotp.TOTP(secret).now()}
    )
    assert res.status_code == 200
    assert res.json()["access_token"]
    assert res.json()["refresh_token"]


async def test_oauth_mfa_verify_with_wrong_code_rejected(client):
    await _register_with_mfa(client, "mfa-oauth-wrong@example.com")
    state = await _google_state(client)

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-mfa-3", "mfa-oauth-wrong@example.com"))

        callback_res = await client.get(
            f"/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
        )

    location = callback_res.headers["location"]
    pending_token = location.split("mfa_pending_token=")[1]

    res = await client.post("/auth/oauth/mfa-verify", json={"pending_token": pending_token, "code": "000000"})
    assert res.status_code == 401
