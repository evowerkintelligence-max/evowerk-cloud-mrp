"""kpi breakdown columns

Revision ID: d4e8f21a6b39
Revises: a2f7c5e91d84
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e8f21a6b39"  # pragma: allowlist secret
down_revision = "a2f7c5e91d84"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kpis", sa.Column("breakdown_group_col", sa.String(length=200), nullable=True))
    op.add_column("kpis", sa.Column("breakdown_agg", sa.String(length=10), nullable=True))
    op.add_column("kpis", sa.Column("breakdown_value_col", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("kpis", "breakdown_value_col")
    op.drop_column("kpis", "breakdown_agg")
    op.drop_column("kpis", "breakdown_group_col")
