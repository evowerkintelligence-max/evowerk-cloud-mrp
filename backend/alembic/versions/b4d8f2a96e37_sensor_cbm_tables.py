"""sensor cbm tables

Revision ID: b4d8f2a96e37
Revises: a9c3e7f15d82
Create Date: 2026-07-18 00:00:01.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b4d8f2a96e37"  # pragma: allowlist secret
down_revision = "a9c3e7f15d82"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_readings_org_machine_sensor_ts", "sensor_readings",
        ["org_id", "machine", "sensor", "ts"],
    )

    op.create_table(
        "sensor_baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("centerline", sa.Float(), nullable=True),
        sa.Column("ucl", sa.Float(), nullable=True),
        sa.Column("lcl", sa.Float(), nullable=True),
        sa.Column("mr_bar", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_baselines_org_machine_sensor", "sensor_baselines",
        ["org_id", "machine", "sensor"], unique=True,
    )

    op.create_table(
        "sensor_anomalies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("sensor", sa.String(length=255), nullable=False),
        sa.Column("reading_id", sa.Uuid(), nullable=False),
        sa.Column("violations", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reading_id"], ["sensor_readings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_anomalies_org_machine_detected", "sensor_anomalies",
        ["org_id", "machine", "detected_at"],
    )

    op.create_table(
        "weibull_fits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("machine", sa.String(length=255), nullable=False),
        sa.Column("shape_k", sa.Float(), nullable=True),
        sa.Column("scale_lambda", sa.Float(), nullable=True),
        sa.Column("n_failures", sa.Integer(), nullable=False),
        sa.Column("n_censored", sa.Integer(), nullable=False),
        sa.Column("fit_tier", sa.String(length=32), nullable=False),
        sa.Column("fit_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_weibull_fits_org_machine", "weibull_fits", ["org_id", "machine"], unique=True,
    )


def downgrade() -> None:
    op.drop_table("weibull_fits")
    op.drop_table("sensor_anomalies")
    op.drop_table("sensor_baselines")
    op.drop_table("sensor_readings")
