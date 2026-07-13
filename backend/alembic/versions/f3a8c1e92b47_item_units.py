"""item units

Revision ID: f3a8c1e92b47
Revises: d5f9a1c73e28
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "f3a8c1e92b47"  # pragma: allowlist secret
down_revision = "d5f9a1c73e28"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("factor_to_base", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "code", name="uq_item_units_item_code"),
    )
    op.create_index("ix_item_units_org_id", "item_units", ["org_id"])
    op.create_index("ix_item_units_item_id", "item_units", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_item_units_item_id", table_name="item_units")
    op.drop_index("ix_item_units_org_id", table_name="item_units")
    op.drop_table("item_units")
