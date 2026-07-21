from __future__ import annotations

import logging

import boto3
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def _send_via_ses(to: str, subject: str, body: str) -> None:
    def _call() -> None:
        client = boto3.client("ses", region_name=settings.aws_region)
        client.send_email(
            Source=settings.ses_from_email,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )

    await run_in_threadpool(_call)


async def send_email(to: str, subject: str, body: str) -> None:
    """Provider-agnostic email interface. AWS deployments route through SES
    (see _send_via_ses, authenticated via the ECS task's IAM role - no static
    SMTP credentials to manage). Non-production environments log instead of
    sending, so local dev and tests don't need real AWS credentials."""
    if settings.env == "production":
        await _send_via_ses(to, subject, body)
        return
    logger.info("send_email (dev, not actually sent): to=%s subject=%s\n%s", to, subject, body)


async def send_password_reset_email(to: str, token: str) -> None:
    link = f"{settings.frontend_base_url}/reset-password?token={token}"
    await send_email(
        to,
        "Reset your Evowerk password",
        f"Click the link below to reset your password. This link expires shortly.\n\n{link}",
    )


async def send_verification_email(to: str, token: str) -> None:
    link = f"{settings.frontend_base_url}/verify-email?token={token}"
    await send_email(
        to,
        "Verify your Evowerk email",
        f"Click the link below to verify your email address.\n\n{link}",
    )
