# backend/alembic/versions/d5b0c4f936e2_wo_assigned_operator.py
"""work order assigned operator

Revision ID: d5b0c4f936e2
Revises: c4a9b3e825d1
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "d5b0c4f936e2"  # pragma: allowlist secret
down_revision = "c4a9b3e825d1"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column(
            "assigned_operator_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("operators.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "assigned_operator_id")
