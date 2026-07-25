"""alert_rules.last_triggered: transition tracking for push notifications

Revision ID: e2f6b9c14a75
Revises: d1e5f8a72c93
Create Date: 2026-07-24

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2f6b9c14a75"  # pragma: allowlist secret
down_revision = "d1e5f8a72c93"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_rules", sa.Column("last_triggered", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("alert_rules", "last_triggered")
