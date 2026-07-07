from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit: int, window_seconds: int):
    """Fixed-window per-IP rate limit backed by Redis, so the count is shared and
    correct across every app instance/worker, not just the process handling this
    request. Fails open (allows the request, logs a warning) if Redis is
    unreachable - an infra blip here shouldn't take down login for everyone."""

    async def _dependency(request: Request, redis_conn: Redis = Depends(get_redis)) -> None:
        key = f"ratelimit:{scope}:{_client_ip(request)}"
        try:
            count = await redis_conn.incr(key)
            if count == 1:
                await redis_conn.expire(key, window_seconds)
        except RedisError:
            logger.warning("rate limiter backend unavailable for scope=%s, failing open", scope)
            return

        if count > limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later"
            )

    return _dependency
