"""audit_logs append-only: DB-level trigger rejects UPDATE/DELETE

Revision ID: 0c1e0833260f
Revises: d6a3d9def5ac
Create Date: 2026-07-07

"""
from alembic import op

revision = "0c1e0833260f"
down_revision = "d6a3d9def5ac"
branch_labels = None
depends_on = None

# Enforced in the database, not just the app layer, so a compromised or buggy
# app can't retroactively edit/erase what it already logged - only a Postgres
# superuser bypassing triggers entirely could (that's the accepted trust
# boundary; see security_roadmap "assume breach" principle).
_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_SQL = """
CREATE TRIGGER trg_audit_logs_immutable
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_FUNCTION_SQL)
    op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_immutable();")
