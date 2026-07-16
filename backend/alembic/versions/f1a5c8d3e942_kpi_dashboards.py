"""kpi dashboards

Revision ID: f1a5c8d3e942
Revises: d4e8f21a6b39
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a5c8d3e942"  # pragma: allowlist secret
down_revision = "d4e8f21a6b39"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpi_dashboards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("kpi_ids", sa.JSON(), nullable=False),
        sa.Column("refresh_seconds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kpi_dashboards_org_id", "kpi_dashboards", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_kpi_dashboards_org_id", table_name="kpi_dashboards")
    op.drop_table("kpi_dashboards")
