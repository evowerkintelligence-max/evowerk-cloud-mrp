"""bom line scrap_pct

Revision ID: d5f9a1c73e28
Revises: b1c4e7a92d38
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "d5f9a1c73e28"  # pragma: allowlist secret
down_revision = "b1c4e7a92d38"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bom_lines",
        sa.Column("scrap_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("bom_lines", "scrap_pct")
