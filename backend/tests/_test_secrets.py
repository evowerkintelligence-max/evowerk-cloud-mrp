"""Shared credentials for the test suite.

One place for the test password instead of a literal per module. Override
with TEST_PASSWORD to exercise a different value; the default satisfies the
password policy (12+ chars, letter + digit).
"""

from __future__ import annotations

import os

TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "correct-horse-battery-staple1")  # pragma: allowlist secret
