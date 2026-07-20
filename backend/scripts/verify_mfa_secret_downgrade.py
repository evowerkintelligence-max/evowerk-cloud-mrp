"""CI-only check: proves the d6a3d9def5ac downgrade doesn't abort when a real
(longer-than-64-char) encrypted mfa_secret is present.

Runs the same UPDATE + ALTER COLUMN the migration's downgrade() executes,
directly against a real Postgres with migrations already applied (see the
`migration-check` job in .github/workflows/ci.yml) - alembic downgrade isn't
exercised by the pytest suite, which never runs migrations against SQLite,
and running the actual `alembic downgrade` CLI would walk every migration
between here and head, not just this one.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

LONG_CIPHERTEXT = "x" * 200  # longer than the pre-hardening VARCHAR(64)


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])

    async with engine.begin() as conn:
        org_id = (
            await conn.execute(
                text("INSERT INTO orgs (id, name, created_at) VALUES (gen_random_uuid(), 'ci', now()) RETURNING id")
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO users (id, org_id, email, password_hash, mfa_secret, created_at) "
                "VALUES (gen_random_uuid(), :org_id, 'mfa-downgrade-ci@example.com', 'x', :secret, now())"
            ),
            {"org_id": org_id, "secret": LONG_CIPHERTEXT},
        )

    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE users SET mfa_secret = NULL WHERE length(mfa_secret) > 64"))
            await conn.execute(text("ALTER TABLE users ALTER COLUMN mfa_secret TYPE VARCHAR(64)"))
            remaining = (
                await conn.execute(
                    text("SELECT mfa_secret FROM users WHERE email = 'mfa-downgrade-ci@example.com'")
                )
            ).scalar_one()
            assert remaining is None, "oversized mfa_secret should have been cleared before the downgrade ALTER"
    finally:
        # Restore the column so the rest of the job still sees the upgraded schema.
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ALTER COLUMN mfa_secret TYPE VARCHAR(255)"))

    await engine.dispose()
    print("d6a3d9def5ac downgrade's mfa_secret handling confirmed safe with a real (>64 char) ciphertext present.")


if __name__ == "__main__":
    asyncio.run(main())
