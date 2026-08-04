"""sync_schedule_tables

Revision ID: 2ff1458f4bf7
Revises: c4d5e6f7a8b9
Create Date: 2026-08-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2ff1458f4bf7"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- rename realtime_streams -> sync_schedules, drop/add columns ---
    op.rename_table("realtime_streams", "sync_schedules")
    op.drop_column("sync_schedules", "accumulate")
    op.add_column("sync_schedules", sa.Column("cursor_column", sa.String(length=255), nullable=True))
    op.add_column("sync_schedules", sa.Column("cursor_value", sa.String(length=255), nullable=True))

    # --- new sync_ticks / sync_rows tables ---
    op.create_table(
        "sync_ticks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["sync_schedules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_ticks_schedule_id", "sync_ticks", ["schedule_id"])
    op.create_index("ix_sync_ticks_org_id", "sync_ticks", ["org_id"])

    op.create_table(
        "sync_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["sync_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_rows_schedule_id", "sync_rows", ["schedule_id"])

    # --- repoint automl_live_bindings.stream_id -> schedule_id (FK now targets sync_schedules) ---
    op.alter_column("automl_live_bindings", "stream_id", new_column_name="schedule_id")
    op.drop_constraint("automl_live_bindings_stream_id_fkey", "automl_live_bindings", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_live_bindings_schedule_id", "automl_live_bindings", "sync_schedules",
        ["schedule_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_index("ix_automl_live_bindings_stream_id", table_name="automl_live_bindings")
    op.create_index("ix_automl_live_bindings_schedule_id", "automl_live_bindings", ["schedule_id"])

    # --- repoint automl_runs.source_stream_id's FK target (column name unchanged) ---
    op.drop_constraint("fk_automl_runs_source_stream_id", "automl_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_runs_source_stream_id", "automl_runs", "sync_schedules",
        ["source_stream_id"], ["id"], ondelete="SET NULL",
    )

    # --- one-time data migration: alert_rules.config "stream_id" -> "schedule_id" ---
    op.execute(
        "UPDATE alert_rules SET config = (config - 'stream_id') || "
        "jsonb_build_object('schedule_id', config->'stream_id') "
        "WHERE type = 'stream_metric' AND config ? 'stream_id'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE alert_rules SET config = (config - 'schedule_id') || "
        "jsonb_build_object('stream_id', config->'schedule_id') "
        "WHERE type = 'stream_metric' AND config ? 'schedule_id'"
    )

    op.drop_constraint("fk_automl_runs_source_stream_id", "automl_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_runs_source_stream_id", "automl_runs", "realtime_streams",
        ["source_stream_id"], ["id"], ondelete="SET NULL",
    )

    op.drop_index("ix_automl_live_bindings_schedule_id", table_name="automl_live_bindings")
    op.create_index("ix_automl_live_bindings_stream_id", "automl_live_bindings", ["stream_id"])
    op.drop_constraint("fk_automl_live_bindings_schedule_id", "automl_live_bindings", type_="foreignkey")
    op.create_foreign_key(
        "automl_live_bindings_stream_id_fkey", "automl_live_bindings", "realtime_streams",
        ["schedule_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("automl_live_bindings", "schedule_id", new_column_name="stream_id")

    op.drop_index("ix_sync_rows_schedule_id", table_name="sync_rows")
    op.drop_table("sync_rows")
    op.drop_index("ix_sync_ticks_org_id", table_name="sync_ticks")
    op.drop_index("ix_sync_ticks_schedule_id", table_name="sync_ticks")
    op.drop_table("sync_ticks")

    op.drop_column("sync_schedules", "cursor_value")
    op.drop_column("sync_schedules", "cursor_column")
    op.add_column("sync_schedules", sa.Column("accumulate", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.rename_table("sync_schedules", "realtime_streams")
