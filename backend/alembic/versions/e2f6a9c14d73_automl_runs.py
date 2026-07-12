"""automl_runs

Revision ID: e2f6a9c14d73
Revises: c6b2f4a83e17
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2f6a9c14d73"  # pragma: allowlist secret
down_revision = "c6b2f4a83e17"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automl_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("target_column", sa.String(length=255), nullable=False),
        sa.Column("feature_columns", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("leaderboard", sa.JSON(), nullable=True),
        sa.Column("best_model_name", sa.String(length=64), nullable=True),
        sa.Column("metric_name", sa.String(length=32), nullable=True),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("model_path", sa.String(length=1000), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_automl_runs_org_id", "automl_runs", ["org_id"])
    op.create_index("ix_automl_runs_dataset_id", "automl_runs", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_automl_runs_dataset_id", table_name="automl_runs")
    op.drop_index("ix_automl_runs_org_id", table_name="automl_runs")
    op.drop_table("automl_runs")
