"""CI-only check: proves audit_logs rejects UPDATE/DELETE at the DB level.

Run against a real Postgres with migrations already applied (see the
`migration-check` job in .github/workflows/ci.yml) - the trigger this checks
for is Postgres-specific and is a no-op on the SQLite database the pytest
suite uses, so it can't be covered there.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    # Deliberately not importing app.core.config here: running this as
    # `python scripts/verify_audit_log_immutable.py` puts the script's own
    # directory on sys.path, not `backend/`, so `import app` fails. Reading
    # the env var directly avoids that entirely - this is CI-only tooling,
    # not part of the app itself.
    engine = create_async_engine(os.environ["DATABASE_URL"])

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_logs (id, action, event_metadata, created_at) "
                "VALUES (gen_random_uuid(), 'ci_check', '{}', now())"
            )
        )

    for label, statement in [
        ("UPDATE", "UPDATE audit_logs SET action = 'tampered'"),
        ("DELETE", "DELETE FROM audit_logs"),
    ]:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
        except Exception as exc:
            if "append-only" not in str(exc):
                raise AssertionError(f"{label} failed for the wrong reason: {exc}") from exc
        else:
            raise AssertionError(f"{label} on audit_logs should have been rejected by the trigger")

    await engine.dispose()
    print("audit_logs is confirmed append-only (UPDATE and DELETE both rejected).")


if __name__ == "__main__":
    asyncio.run(main())
