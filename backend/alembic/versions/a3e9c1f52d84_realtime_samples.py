"""realtime samples + external stream metrics

Revision ID: a3e9c1f52d84
Revises: d5b0c4f936e2
Create Date: 2026-07-30 00:00:01.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a3e9c1f52d84"  # pragma: allowlist secret
down_revision = "d5b0c4f936e2"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_samples",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("rows_in_window", sa.Integer(), nullable=False),
        sa.Column("rows_collected", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("last_tick_s", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("raw_rows", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stream_id"], ["realtime_streams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_realtime_samples_org_id", "realtime_samples", ["org_id"])
    op.create_index("ix_realtime_samples_stream_id_id", "realtime_samples", ["stream_id", "id"])
    # The hourly retention sweep deletes by ts alone; without this it is a full
    # scan plus a TOAST read of every raw_rows blob.
    op.create_index("ix_realtime_samples_ts", "realtime_samples", ["ts"])

    op.create_table(
        "realtime_stream_metrics",
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["stream_id"], ["realtime_streams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("stream_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("realtime_stream_metrics")
    op.drop_index("ix_realtime_samples_ts", table_name="realtime_samples")
    op.drop_index("ix_realtime_samples_stream_id_id", table_name="realtime_samples")
    op.drop_index("ix_realtime_samples_org_id", table_name="realtime_samples")
    op.drop_table("realtime_samples")
