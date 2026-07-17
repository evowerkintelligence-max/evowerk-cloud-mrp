# backend/alembic/versions/d4f7a1c92b56_pace_tracking.py
"""pace tracking sessions + taps

Revision ID: d4f7a1c92b56
Revises: f3a8b2e7c641
Create Date: 2026-07-15

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f7a1c92b56"  # pragma: allowlist secret
down_revision = "f3a8b2e7c641"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pace_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("line", sa.String(length=255), nullable=False),
        sa.Column("operator", sa.String(length=255), nullable=False),
        sa.Column("target_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pause_reason", sa.String(length=16), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unplanned_dt_min", sa.Float(), nullable=False),
        sa.Column("planned_dt_min", sa.Float(), nullable=False),
        sa.Column("other_dt_min", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pace_sessions_org_id", "pace_sessions", ["org_id"])

    op.create_table(
        "pace_taps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["pace_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pace_taps_session_id", "pace_taps", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_pace_taps_session_id", table_name="pace_taps")
    op.drop_table("pace_taps")
    op.drop_index("ix_pace_sessions_org_id", table_name="pace_sessions")
    op.drop_table("pace_sessions")
