"""Shared credentials for the test suite.

Generated per run, so the repo holds no reusable password and nothing can
depend on a known value. Set TEST_PASSWORD to pin one when debugging.
"""

from __future__ import annotations

import os
import secrets

# Letter + digit suffix keeps the random value inside the password policy
# (12+ chars, at least one letter and one digit).
TEST_PASSWORD = os.environ.get("TEST_PASSWORD") or secrets.token_urlsafe(18) + "a1"
# Never matches TEST_PASSWORD: for wrong-password and unknown-user paths.
WRONG_PASSWORD = secrets.token_urlsafe(18) + "b2"
# Policy violations: too short, and long enough but without a digit.
TOO_SHORT_PASSWORD = "a1" * 3  # pragma: allowlist secret
NO_DIGIT_PASSWORD = "x" * 20  # pragma: allowlist secret
