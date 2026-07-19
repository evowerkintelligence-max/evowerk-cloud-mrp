"""bom one-active-per-item constraint + explicit fk ondelete

Revision ID: b698cceefeda
Revises: 80403c683082
Create Date: 2026-07-19

"""
from alembic import op
import sqlalchemy as sa

revision = "b698cceefeda"  # pragma: allowlist secret
down_revision = "61636a61b404"  # pragma: allowlist secret
branch_labels = None
depends_on = None

# The original create_table (ad775f9b6888) declared these FKs inline with no
# explicit constraint name, so Postgres assigned its own default
# <table>_<column>_fkey name. Rather than hard-code that assumption, look the
# constraint up by table+column at migration time and drop whatever it's
# actually called before adding the RESTRICT one back under a name we control.
_DROP_UNNAMED_FK = """
DO $$
DECLARE
    found_name text;
BEGIN
    SELECT tc.constraint_name INTO found_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    WHERE tc.table_schema = current_schema()
      AND tc.table_name = '{table}'
      AND tc.constraint_type = 'FOREIGN KEY'
      AND kcu.column_name = '{column}';
    IF found_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', found_name);
    END IF;
END $$;
"""


def upgrade() -> None:
    # One-active-BOM-per-item, enforced at the DB level as a backstop for the
    # app-level invariant activate_bom already maintains (see app/models/bom.py).
    op.create_index(
        "uq_boms_org_item_active",
        "boms",
        ["org_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.execute(_DROP_UNNAMED_FK.format(table="bom_lines", column="component_item_id"))
    op.create_foreign_key(
        "fk_bom_lines_component_item_id",
        "bom_lines",
        "items",
        ["component_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(_DROP_UNNAMED_FK.format(table="boms", column="created_by"))
    op.create_foreign_key(
        "fk_boms_created_by",
        "boms",
        "users",
        ["created_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_boms_created_by", "boms", type_="foreignkey")
    op.create_foreign_key("fk_boms_created_by", "boms", "users", ["created_by"], ["id"])

    op.drop_constraint("fk_bom_lines_component_item_id", "bom_lines", type_="foreignkey")
    op.create_foreign_key(
        "fk_bom_lines_component_item_id", "bom_lines", "items", ["component_item_id"], ["id"]
    )

    op.drop_index("uq_boms_org_item_active", table_name="boms")
