"""shift forecast source datasets

Revision ID: b6d3f8a17c92
Revises: f1a5c8d3e942
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d3f8a17c92"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_forecast_datasets",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("shift", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id", "shift"),
    )


def downgrade() -> None:
    op.drop_table("shift_forecast_datasets")
