from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_email_dev_env_logs_only(caplog):
    from app.core.email import send_email

    with caplog.at_level("INFO"):
        await send_email("someone@example.com", "Subject", "Body text")

    assert "someone@example.com" in caplog.text
    assert "Subject" in caplog.text


async def test_send_password_reset_email_builds_link_with_token():
    from app.core.email import send_password_reset_email

    with patch("app.core.email.send_email") as mock_send:
        await send_password_reset_email("someone@example.com", "the-token-123")

    mock_send.assert_awaited_once()
    to, subject, body = mock_send.await_args.args
    assert to == "someone@example.com"
    assert "the-token-123" in body


async def test_ses_sender_calls_boto3_send_email():
    from app.core.email import _send_via_ses

    with patch("app.core.email.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        await _send_via_ses("someone@example.com", "Subject", "Body text")

    mock_client_factory.assert_called_once_with("ses", region_name="us-east-1")
    mock_client.send_email.assert_called_once()
    call_kwargs = mock_client.send_email.call_args.kwargs
    assert call_kwargs["Destination"]["ToAddresses"] == ["someone@example.com"]
    assert call_kwargs["Message"]["Subject"]["Data"] == "Subject"
