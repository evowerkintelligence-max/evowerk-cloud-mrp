from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging import configure_logging, init_error_tracking
from app.core.security import create_access_token
from app.middleware.request_context import RequestContextMiddleware


@pytest.fixture
def json_logs():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    configure_logging()
    buf = io.StringIO()
    root.handlers[0].setStream(buf)
    yield lambda: [json.loads(line) for line in buf.getvalue().splitlines()]
    root.handlers, root.level = saved


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ok")
    def ok():
        logging.getLogger("test").info("handled")
        return {}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    return app


async def test_log_lines_carry_request_and_org_id(json_logs):
    token = create_access_token(user_id="u1", org_id="org-123")
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/ok", headers={"X-Request-ID": "req-abc", "Authorization": f"Bearer {token}"})
    assert r.headers["X-Request-ID"] == "req-abc"
    line = next(entry for entry in json_logs() if entry["message"] == "handled")
    assert line["level"] == "INFO"
    assert line["request_id"] == "req-abc"
    assert line["org_id"] == "org-123"


async def test_unhandled_exception_is_logged_with_request_id(json_logs):
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/boom", headers={"X-Request-ID": "req-err"})
    assert r.status_code == 500
    line = next(entry for entry in json_logs() if entry["message"] == "unhandled error")
    assert line["level"] == "ERROR"
    assert line["request_id"] == "req-err"
    assert line["path"] == "/boom"
    assert "RuntimeError: kaboom" in line["exc_info"]


def test_error_tracking_disabled_without_dsn():
    assert init_error_tracking("", "test") is False
