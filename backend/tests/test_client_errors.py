from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import logging

import pytest

from app.core.security import decode_token

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

_VALID = {"kind": "render", "message": "boom", "stack": "Error: boom\n  at X", "url": "/items"}


async def test_unauthenticated_report_is_accepted_and_logged(client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.api.routes.client_errors"):
        res = await client.post("/client-errors", json=_VALID)
    assert res.status_code == 204
    [record] = [r for r in caplog.records if r.name == "app.api.routes.client_errors"]
    assert record.levelno == logging.WARNING
    assert record.client_error_kind == "render"
    assert record.client_error_message == "boom"
    assert record.client_error_url == "/items"
    assert record.user_id is None


async def test_authenticated_report_attaches_user_id(client, caplog):
    await client.post("/auth/register", json={"org_name": "Acme", "email": "o@example.com", "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": "o@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    with caplog.at_level(logging.WARNING, logger="app.api.routes.client_errors"):
        res = await client.post(
            "/client-errors",
            json={**_VALID, "kind": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert res.status_code == 204
    [record] = [r for r in caplog.records if r.name == "app.api.routes.client_errors"]
    assert record.user_id == decode_token(token)["sub"]


async def test_bad_token_is_ignored_not_rejected(client):
    res = await client.post("/client-errors", json=_VALID, headers={"Authorization": "Bearer nope"})
    assert res.status_code == 204


@pytest.mark.parametrize(
    "override",
    [
        {"message": "x" * 1001},
        {"stack": "x" * 8001},
        {"url": "x" * 501},
        {"kind": "bogus"},
    ],
)
async def test_oversized_or_invalid_payload_is_rejected(client, override):
    res = await client.post("/client-errors", json={**_VALID, **override})
    assert res.status_code == 422


async def test_sentry_capture_runs_only_when_initialized(client, monkeypatch):
    import sentry_sdk

    captured = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, **kw: captured.append(msg))
    await client.post("/client-errors", json=_VALID)
    assert captured == []

    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: True)
    await client.post("/client-errors", json=_VALID)
    assert captured == ["[client:render] boom"]
