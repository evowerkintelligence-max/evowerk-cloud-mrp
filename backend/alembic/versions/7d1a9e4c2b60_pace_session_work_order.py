"""pace session work order

Revision ID: 7d1a9e4c2b60
Revises: 3caf4cd09df8
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "7d1a9e4c2b60"  # pragma: allowlist secret
down_revision = "3caf4cd09df8"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: every session that already exists predates the link, and a
    # session started the free-text way never gets one.
    op.add_column(
        "pace_sessions",
        sa.Column(
            "work_order_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("work_orders.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pace_sessions", "work_order_id")
