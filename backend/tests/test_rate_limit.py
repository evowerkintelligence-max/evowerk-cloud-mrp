from __future__ import annotations

import pytest

from app.core.config import get_settings
from tests.test_auth import PASSWORD, _register

pytestmark = pytest.mark.asyncio


async def test_many_concurrent_users_behind_one_ip_are_not_blocked(client):
    """The rate limiter must not get in the way of ordinary usage: many real users
    can share one public IP (office NAT, VPN). httpx's ASGITransport reports every
    request as coming from the same client, so this doubles as a same-IP test."""
    settings = get_settings()
    num_users = 15
    assert num_users < settings.rate_limit_login_per_5min, "test assumes headroom under the limit"

    for i in range(num_users):
        email = f"concurrent-user-{i}@example.com"
        await _register(client, email=email)
        res = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert res.status_code == 200, f"user {i} should not be rate-limited"


async def test_login_rate_limit_trips_per_ip_after_threshold(client):
    """Distinct, never-registered emails so the per-account lockout (which fires
    after 5 failures on the *same* account) can't be what's responsible for any
    429s seen here - only the per-IP counter is shared across these requests."""
    settings = get_settings()
    limit = settings.rate_limit_login_per_5min

    last_status = None
    for i in range(limit + 5):
        res = await client.post(
            "/auth/login",
            json={"email": f"never-registered-{i}@example.com", "password": "whatever-guess-1"},
        )
        last_status = res.status_code
        if last_status == 429:
            break

    assert last_status == 429, "expected the per-IP rate limit to trip before exhausting the loop"
