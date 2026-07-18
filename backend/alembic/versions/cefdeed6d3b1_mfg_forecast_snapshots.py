"""mfg_forecast_snapshots (forecast track record)

Revision ID: cefdeed6d3b1
Revises: f1a5c8d3e942
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cefdeed6d3b1"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mfg_forecast_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        # No FK to datasets.id on purpose - informational only (which upload
        # produced this point). Datasets are immutable-after-create, so a
        # re-upload gets a new id; the lookup key below is dataset-independent
        # so a deleted/replaced dataset doesn't lose forecast track record.
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("target_col", sa.String(length=255), nullable=False),
        sa.Column("machine_filter", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("predicted", sa.Float(), nullable=False),
        sa.Column("lower", sa.Float(), nullable=False),
        sa.Column("upper", sa.Float(), nullable=False),
        sa.Column("made_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mfg_forecast_snapshots_lookup",
        "mfg_forecast_snapshots",
        ["org_id", "target_col", "machine_filter", "date"],
    )


def downgrade() -> None:
    op.drop_index("ix_mfg_forecast_snapshots_lookup", table_name="mfg_forecast_snapshots")
    op.drop_table("mfg_forecast_snapshots")
