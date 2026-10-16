from __future__ import annotations

import logging
from typing import Literal

import jwt
import sentry_sdk
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import decode_token
from app.middleware.rate_limit import rate_limit

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client-errors", tags=["client-errors"])
_client_errors_rate_limit = rate_limit("client_errors", settings.rate_limit_client_errors_per_5min, 300)


class ClientErrorReport(BaseModel):
    kind: Literal["render", "unhandled", "rejection", "api"]
    message: str = Field(..., max_length=1000)
    stack: str | None = Field(None, max_length=8000)
    url: str = Field("", max_length=500)
    release: str | None = Field(None, max_length=100)
    user_agent: str | None = Field(None, max_length=500)


def _user_id(request: Request) -> str | None:
    # Optional auth: errors can happen before login, and navigator.sendBeacon
    # can't send an Authorization header at all. org_id is already on every
    # log line via RequestContextMiddleware.
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = decode_token(auth[7:])
    except jwt.PyJWTError:
        return None
    return payload.get("sub") if payload.get("type") == "access" else None


@router.post("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_client_errors_rate_limit)])
async def report_client_error(payload: ClientErrorReport, request: Request) -> None:
    user_id = _user_id(request)
    user_agent = payload.user_agent or request.headers.get("user-agent", "")[:500]
    logger.warning(
        "client error",
        extra={
            "client_error_kind": payload.kind,
            "client_error_message": payload.message,
            "client_error_stack": payload.stack,
            "client_error_url": payload.url,
            "client_release": payload.release,
            "user_agent": user_agent,
            "user_id": user_id,
        },
    )
    if sentry_sdk.is_initialized():
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", "client")
            scope.set_tag("client_error_kind", payload.kind)
            if payload.release:
                scope.set_tag("client_release", payload.release)
            scope.set_extra("url", payload.url)
            scope.set_extra("stack", payload.stack)
            if user_id:
                scope.set_user({"id": user_id})
            sentry_sdk.capture_message(f"[client:{payload.kind}] {payload.message}", level="warning")
