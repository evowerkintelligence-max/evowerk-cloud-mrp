"""forecast integration: demand_forecasts

Revision ID: b7c1e9a4f2d8
Revises: 4fe92fc85cfd
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "b7c1e9a4f2d8"
down_revision = "4fe92fc85cfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demand_forecasts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_demand_forecasts_org_id", "demand_forecasts", ["org_id"])
    op.create_index("ix_demand_forecasts_item_id", "demand_forecasts", ["item_id"])
    op.create_index("ix_demand_forecasts_period_start", "demand_forecasts", ["period_start"])
    op.create_unique_constraint(
        "uq_demand_forecasts_org_item_period", "demand_forecasts", ["org_id", "item_id", "period_start"]
    )


def downgrade() -> None:
    op.drop_table("demand_forecasts")
