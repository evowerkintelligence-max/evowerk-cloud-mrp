"""automl_template_id

Revision ID: 5f52546f02e4
Revises: e2f6a9c14d73
Create Date: 2026-07-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5f52546f02e4"  # pragma: allowlist secret
down_revision = "e2f6a9c14d73"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automl_runs", sa.Column("template_id", sa.String(length=64), nullable=True))
    op.create_index("ix_automl_runs_template_id", "automl_runs", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_automl_runs_template_id", table_name="automl_runs")
    op.drop_column("automl_runs", "template_id")
