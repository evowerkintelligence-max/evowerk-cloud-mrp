"""item description and barcode

Revision ID: 24a3df0522d7
Revises: 8cbfff0295b7
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "24a3df0522d7"  # pragma: allowlist secret
down_revision = "8cbfff0295b7"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("barcode", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_items_org_barcode", "items", ["org_id", "barcode"])


def downgrade() -> None:
    op.drop_constraint("uq_items_org_barcode", "items", type_="unique")
    op.drop_column("items", "barcode")
    op.drop_column("items", "description")
