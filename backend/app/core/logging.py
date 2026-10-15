"""Structured JSON logging and opt-in error tracking.

Every log line is one JSON object on stdout carrying the current request's
`request_id` and `org_id` (set by app.middleware.request_context), so a
customer's own log pipeline (CloudWatch, Azure Monitor, Loki...) can filter
one request or one tenant without regex-parsing free text.

Error tracking (Sentry) is off unless SENTRY_DSN is set. EvoWerk never hosts
customer data, so the DSN is the customer's own (self-hosted Sentry or their
own account); send_default_pii stays False either way.
"""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
org_id_var: ContextVar[str | None] = ContextVar("org_id", default=None)


class _RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.org_id = org_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestContextFilter())
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(org_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn installs its own plain-text handlers before importing the app;
    # route its loggers through the JSON handler above instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def init_error_tracking(dsn: str, environment: str) -> bool:
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=environment, send_default_pii=False, traces_sample_rate=0.0)
    return True
