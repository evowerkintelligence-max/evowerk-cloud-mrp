from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.core import users as users_module
from app.core.config import get_settings

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _drain_background_tasks() -> None:
    """The email send in on_after_forgot_password/on_after_request_verify is
    now fire-and-forget (see users_module._fire_and_forget) so the response
    doesn't block on it - tests asserting the email was sent need to wait for
    it explicitly instead of relying on it having finished by request-return."""
    await asyncio.gather(*users_module._background_tasks)


async def test_forgot_password_always_returns_202_even_for_unknown_email(client):
    # fastapi-users' forgot-password endpoint deliberately returns 202 regardless
    # of whether the email exists, so a caller can't enumerate registered emails.
    res = await client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert res.status_code == 202


async def test_forgot_password_then_reset_changes_password(client):
    await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "reset@example.com", "password": PASSWORD}
    )

    with patch("app.core.email.send_email") as mock_send:
        res = await client.post("/auth/forgot-password", json={"email": "reset@example.com"})
        await _drain_background_tasks()
    assert res.status_code == 202
    mock_send.assert_awaited_once()
    reset_link_body = mock_send.await_args.args[2]
    token = reset_link_body.split("token=")[1].split()[0].strip()

    new_password = "a-brand-new-password-1"  # pragma: allowlist secret
    reset_res = await client.post(
        "/auth/reset-password", json={"token": token, "password": new_password}
    )
    assert reset_res.status_code == 200

    old_login = await client.post("/auth/login", json={"email": "reset@example.com", "password": PASSWORD})
    assert old_login.status_code == 401

    new_login = await client.post(
        "/auth/login", json={"email": "reset@example.com", "password": new_password}
    )
    assert new_login.status_code == 200


async def test_request_verify_then_verify_marks_user_verified(client):
    await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "verify@example.com", "password": PASSWORD}
    )

    with patch("app.core.email.send_email") as mock_send:
        res = await client.post("/auth/request-verify-token", json={"email": "verify@example.com"})
        await _drain_background_tasks()
    assert res.status_code == 202
    verify_link_body = mock_send.await_args.args[2]
    token = verify_link_body.split("token=")[1].split()[0].strip()

    verify_res = await client.post("/auth/verify", json={"token": token})
    assert verify_res.status_code == 200
    assert verify_res.json()["is_verified"] is True


async def test_forgot_password_rate_limit_trips_per_ip_after_threshold(client):
    """/auth/forgot-password shares the "register" rate-limit scope (same risk
    class: unauthenticated, email-triggering) - it must trip just like
    /auth/register does after rate_limit_register_per_hour requests."""
    settings = get_settings()
    limit = settings.rate_limit_register_per_hour

    last_status = None
    for i in range(limit + 1):
        res = await client.post("/auth/forgot-password", json={"email": f"nobody-{i}@example.com"})
        last_status = res.status_code
        if last_status == 429:
            break

    assert last_status == 429, "expected the per-IP rate limit to trip before exhausting the loop"
