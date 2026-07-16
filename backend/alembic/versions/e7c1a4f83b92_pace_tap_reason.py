# backend/alembic/versions/e7c1a4f83b92_pace_tap_reason.py
"""pace_taps.reason column

Revision ID: e7c1a4f83b92
Revises: a1c8e4f92d67
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7c1a4f83b92"  # pragma: allowlist secret
down_revision = "a1c8e4f92d67"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pace_taps", sa.Column("reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("pace_taps", "reason")
