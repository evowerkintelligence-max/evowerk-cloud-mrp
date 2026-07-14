"""bom line unit conversion

Revision ID: a7c92e4f18d5
Revises: f3a8c1e92b47
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "a7c92e4f18d5"  # pragma: allowlist secret
down_revision = "f3a8c1e92b47"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bom_lines", sa.Column("unit_id", sa.Uuid(), nullable=True))
    op.add_column("bom_lines", sa.Column("unit_quantity", sa.Numeric(12, 4), nullable=True))
    op.create_foreign_key(
        "fk_bom_lines_unit_id", "bom_lines", "item_units", ["unit_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_bom_lines_unit_id", "bom_lines", type_="foreignkey")
    op.drop_column("bom_lines", "unit_quantity")
    op.drop_column("bom_lines", "unit_id")
