from __future__ import annotations

import logging
import uuid

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import org_id_var, request_id_var
from app.core.security import decode_token

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tags every log line in a request with request_id/org_id, echoes
    X-Request-ID back to the caller, and logs unhandled exceptions with both."""

    async def dispatch(self, request: Request, call_next):
        # Caller-supplied IDs are capped so a hostile header can't bloat logs.
        request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        org_id = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                org_id = decode_token(auth[7:]).get("org_id")
            except jwt.PyJWTError:
                pass
        request_id_token = request_id_var.set(request_id)
        org_id_token = org_id_var.set(org_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
            raise
        finally:
            request_id_var.reset(request_id_token)
            org_id_var.reset(org_id_token)
        response.headers["X-Request-ID"] = request_id
        return response
