"""automl run retrain columns

Revision ID: c2d7e9f1a3b4
Revises: b6d3f8a17c92
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2d7e9f1a3b4"  # pragma: allowlist secret
down_revision = "b6d3f8a17c92"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automl_runs",
        sa.Column("auto_retrain", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("automl_runs", sa.Column("retrained_from_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_automl_runs_retrained_from_run_id", "automl_runs", "automl_runs",
        ["retrained_from_run_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "automl_runs",
        sa.Column("auto_retrain_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_constraint("fk_automl_runs_retrained_from_run_id", "automl_runs", type_="foreignkey")
    op.drop_column("automl_runs", "auto_retrain_streak")
    op.drop_column("automl_runs", "retrained_from_run_id")
    op.drop_column("automl_runs", "auto_retrain")
