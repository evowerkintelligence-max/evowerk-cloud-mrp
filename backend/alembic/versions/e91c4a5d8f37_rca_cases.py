"""rca_cases

Revision ID: e91c4a5d8f37
Revises: c4f8a2e60d97
Create Date: 2026-07-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e91c4a5d8f37"  # pragma: allowlist secret
down_revision = "c4f8a2e60d97"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rca_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("problem_statement", sa.String(length=4000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("trigger_context", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rca_cases_org_id", "rca_cases", ["org_id"])
    op.create_index("ix_rca_cases_dataset_id", "rca_cases", ["dataset_id"])

    op.create_table(
        "rca_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("node_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["rca_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["rca_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rca_nodes_org_id", "rca_nodes", ["org_id"])
    op.create_index("ix_rca_nodes_case_id", "rca_nodes", ["case_id"])

    op.create_table(
        "rca_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("due_date", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Uuid(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["rca_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["rca_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["completed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rca_actions_org_id", "rca_actions", ["org_id"])
    op.create_index("ix_rca_actions_case_id", "rca_actions", ["case_id"])

    op.create_table(
        "rca_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["rca_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rca_events_org_id", "rca_events", ["org_id"])
    op.create_index("ix_rca_events_case_id", "rca_events", ["case_id"])
    op.create_index("ix_rca_events_created_at", "rca_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_rca_events_created_at", table_name="rca_events")
    op.drop_index("ix_rca_events_case_id", table_name="rca_events")
    op.drop_index("ix_rca_events_org_id", table_name="rca_events")
    op.drop_table("rca_events")

    op.drop_index("ix_rca_actions_case_id", table_name="rca_actions")
    op.drop_index("ix_rca_actions_org_id", table_name="rca_actions")
    op.drop_table("rca_actions")

    op.drop_index("ix_rca_nodes_case_id", table_name="rca_nodes")
    op.drop_index("ix_rca_nodes_org_id", table_name="rca_nodes")
    op.drop_table("rca_nodes")

    op.drop_index("ix_rca_cases_dataset_id", table_name="rca_cases")
    op.drop_index("ix_rca_cases_org_id", table_name="rca_cases")
    op.drop_table("rca_cases")
