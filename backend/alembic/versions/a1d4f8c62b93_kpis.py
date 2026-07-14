"""kpis

Revision ID: a1d4f8c62b93
Revises: e2f6a9c14d73
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1d4f8c62b93"  # pragma: allowlist secret
down_revision = "e2f6a9c14d73"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpis",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("sql", sa.String(length=4000), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("eval_policy", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kpis_org_id", "kpis", ["org_id"])
    op.create_index("ix_kpis_dataset_id", "kpis", ["dataset_id"])

    op.create_table(
        "kpi_values",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kpi_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kpi_id"], ["kpis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kpi_values_org_id", "kpi_values", ["org_id"])
    op.create_index("ix_kpi_values_kpi_id", "kpi_values", ["kpi_id"])
    op.create_index("ix_kpi_values_dataset_id", "kpi_values", ["dataset_id"])
    op.create_index("ix_kpi_values_computed_at", "kpi_values", ["computed_at"])
    op.create_index(
        "ix_kpi_values_kpi_dataset_computed_at", "kpi_values", ["kpi_id", "dataset_id", "computed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_kpi_values_kpi_dataset_computed_at", table_name="kpi_values")
    op.drop_index("ix_kpi_values_computed_at", table_name="kpi_values")
    op.drop_index("ix_kpi_values_dataset_id", table_name="kpi_values")
    op.drop_index("ix_kpi_values_kpi_id", table_name="kpi_values")
    op.drop_index("ix_kpi_values_org_id", table_name="kpi_values")
    op.drop_table("kpi_values")

    op.drop_index("ix_kpis_dataset_id", table_name="kpis")
    op.drop_index("ix_kpis_org_id", table_name="kpis")
    op.drop_table("kpis")
