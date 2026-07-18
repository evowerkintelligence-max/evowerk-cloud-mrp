"""merge_scenarios_into_simulation

Revision ID: 61636a61b404
Revises: f1a5c8d3e942
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "61636a61b404"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulations_org_id", "simulations", ["org_id"])
    op.create_index("ix_simulations_dataset_id", "simulations", ["dataset_id"])

    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("simulation_id", sa.Uuid(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["simulation_id"], ["simulations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulation_runs_org_id", "simulation_runs", ["org_id"])
    op.create_index("ix_simulation_runs_simulation_id", "simulation_runs", ["simulation_id"])

    op.drop_index("ix_what_if_scenario_runs_scenario_id", table_name="what_if_scenario_runs")
    op.drop_index("ix_what_if_scenario_runs_org_id", table_name="what_if_scenario_runs")
    op.drop_table("what_if_scenario_runs")

    op.drop_index("ix_what_if_scenarios_dataset_id", table_name="what_if_scenarios")
    op.drop_index("ix_what_if_scenarios_org_id", table_name="what_if_scenarios")
    op.drop_table("what_if_scenarios")


def downgrade() -> None:
    op.create_table(
        "what_if_scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("base_query", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_what_if_scenarios_org_id", "what_if_scenarios", ["org_id"])
    op.create_index("ix_what_if_scenarios_dataset_id", "what_if_scenarios", ["dataset_id"])

    op.create_table(
        "what_if_scenario_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scenario_id"], ["what_if_scenarios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_what_if_scenario_runs_org_id", "what_if_scenario_runs", ["org_id"])
    op.create_index("ix_what_if_scenario_runs_scenario_id", "what_if_scenario_runs", ["scenario_id"])

    op.drop_index("ix_simulation_runs_simulation_id", table_name="simulation_runs")
    op.drop_index("ix_simulation_runs_org_id", table_name="simulation_runs")
    op.drop_table("simulation_runs")

    op.drop_index("ix_simulations_dataset_id", table_name="simulations")
    op.drop_index("ix_simulations_org_id", table_name="simulations")
    op.drop_table("simulations")
