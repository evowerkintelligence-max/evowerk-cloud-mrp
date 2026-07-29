"""capture events

Revision ID: c4a9b3e825d1
Revises: b3f8a2d914c7
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa

revision = "c4a9b3e825d1"  # pragma: allowlist secret
down_revision = "b3f8a2d914c7"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operator_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("operators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("machines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_capture_events_org_id", "capture_events", ["org_id"])
    op.create_index("ix_capture_events_operator_id", "capture_events", ["operator_id"])
    op.create_index("ix_capture_events_machine_id", "capture_events", ["machine_id"])


def downgrade() -> None:
    op.drop_table("capture_events")
