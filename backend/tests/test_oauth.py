from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_google_authorize_redirects_to_google(client):
    with patch("app.api.routes.oauth.google_client") as mock_client:
        mock_client.get_authorization_url = AsyncMock(return_value="https://accounts.google.com/o/oauth2/auth?...")
        res = await client.get("/auth/google/authorize", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "accounts.google.com" in res.headers["location"]


async def test_google_callback_logs_in_existing_account_when_email_verified(client):
    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "provisioned@example.com", "password": "correct-horse-battery-staple1"},  # pragma: allowlist secret
    )

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-1", "provisioned@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert "access_token=" in location
    assert "refresh_token=" in location


async def test_google_callback_rejects_unknown_email(client):
    from fastapi import status

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=True)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-unknown", "nobody@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    assert res.status_code == status.HTTP_403_FORBIDDEN


async def test_google_callback_rejects_auto_link_when_email_not_verified(client):
    from fastapi import status

    await client.post(
        "/auth/register",
        json={"org_name": "Acme", "email": "existing@example.com", "password": "correct-horse-battery-staple1"},  # pragma: allowlist secret
    )

    with (
        patch("app.api.routes.oauth.google_client") as mock_client,
        patch("app.api.routes.oauth._google_email_verified", AsyncMock(return_value=False)),
    ):
        mock_client.get_access_token = AsyncMock(return_value={"access_token": "fake-token", "expires_in": 3600})
        mock_client.get_id_email = AsyncMock(return_value=("google-account-id-2", "existing@example.com"))

        res = await client.get("/auth/google/callback?code=fake-code&state=fake-state", follow_redirects=False)

    # Must NOT silently log in as the existing password account. Because
    # `email` is a unique column, "don't auto-link" can't mean "create a
    # second User row with the same email" either - it must mean "reject
    # the OAuth login" (no tokens issued) rather than trusting an
    # unverified provider email that happens to match an existing account.
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # The existing account itself is untouched - its real password still works.
    login = await client.post(
        "/auth/login", json={"email": "existing@example.com", "password": "correct-horse-battery-staple1"}  # pragma: allowlist secret
    )
    assert login.status_code == 200
