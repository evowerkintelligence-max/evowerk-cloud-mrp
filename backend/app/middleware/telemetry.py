from __future__ import annotations

import time
import uuid

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.security import decode_token
from app.db import session as db_session
from app.models.telemetry import UsageEvent

_SKIP_PATHS = {"/health", "/docs", "/openapi.json"}


class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        if request.url.path in _SKIP_PATHS:
            return response

        org_id = actor_user_id = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                payload = decode_token(auth[7:])
                org_id = uuid.UUID(payload["org_id"])
                actor_user_id = uuid.UUID(payload["sub"])
            except (jwt.PyJWTError, KeyError, ValueError):
                pass

        duration_ms = int((time.monotonic() - start) * 1000)
        async with db_session.async_session_factory() as db:
            db.add(
                UsageEvent(
                    org_id=org_id,
                    actor_user_id=actor_user_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            )
            await db.commit()

        return response
