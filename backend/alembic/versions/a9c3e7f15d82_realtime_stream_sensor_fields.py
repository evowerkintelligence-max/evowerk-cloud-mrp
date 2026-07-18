"""realtime stream sensor fields

Revision ID: a9c3e7f15d82
Revises: f1a5c8d3e942
Create Date: 2026-07-18 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a9c3e7f15d82"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("realtime_streams", sa.Column("machine", sa.String(length=255), nullable=True))
    op.add_column(
        "realtime_streams",
        sa.Column("sensor_map", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("realtime_streams", "sensor_map")
    op.drop_column("realtime_streams", "machine")
