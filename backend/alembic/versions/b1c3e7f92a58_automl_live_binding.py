"""automl_live_binding

Revision ID: b1c3e7f92a58
Revises: 5f52546f02e4
Create Date: 2026-07-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c3e7f92a58"  # pragma: allowlist secret
down_revision = "5f52546f02e4"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automl_runs", sa.Column("source_stream_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_automl_runs_source_stream_id", "automl_runs", "realtime_streams",
        ["source_stream_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "automl_live_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("drift_severity_trigger", sa.String(length=16), nullable=False),
        sa.Column("drift_check_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_drift_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_drift_status", sa.String(length=16), nullable=True),
        sa.Column("auto_retrain_on_drift", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["automl_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stream_id"], ["realtime_streams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_automl_live_bindings_org_id", "automl_live_bindings", ["org_id"])
    op.create_index("ix_automl_live_bindings_run_id", "automl_live_bindings", ["run_id"])
    op.create_index("ix_automl_live_bindings_stream_id", "automl_live_bindings", ["stream_id"])


def downgrade() -> None:
    op.drop_index("ix_automl_live_bindings_stream_id", table_name="automl_live_bindings")
    op.drop_index("ix_automl_live_bindings_run_id", table_name="automl_live_bindings")
    op.drop_index("ix_automl_live_bindings_org_id", table_name="automl_live_bindings")
    op.drop_table("automl_live_bindings")

    op.drop_constraint("fk_automl_runs_source_stream_id", "automl_runs", type_="foreignkey")
    op.drop_column("automl_runs", "source_stream_id")
