"""bi dashboards (dashboards + charts)

Revision ID: b7e14a2f9d63
Revises: a3d5e8f14c6b
Create Date: 2026-07-11

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e14a2f9d63"  # pragma: allowlist secret
down_revision = "a3d5e8f14c6b"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dashboards_org_id", "dashboards", ["org_id"])

    op.create_table(
        "charts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dashboard_id", sa.Uuid(), nullable=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("chart_type", sa.String(length=16), nullable=False),
        sa.Column("query_config", sa.JSON(), nullable=False),
        sa.Column("category_field", sa.String(length=255), nullable=False),
        sa.Column("value_field", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_charts_org_id", "charts", ["org_id"])
    op.create_index("ix_charts_dashboard_id", "charts", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_charts_dashboard_id", table_name="charts")
    op.drop_index("ix_charts_org_id", table_name="charts")
    op.drop_table("charts")
    op.drop_index("ix_dashboards_org_id", table_name="dashboards")
    op.drop_table("dashboards")
