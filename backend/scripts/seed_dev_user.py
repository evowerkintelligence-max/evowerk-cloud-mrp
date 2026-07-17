"""Idempotent dev/test login - creates one fixed-credential user for local
development, so registering by hand isn't required every time the db_data
volume is reset.

Run: docker compose exec backend python scripts/seed_dev_user.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# `python scripts/seed_dev_user.py` puts scripts/ on sys.path, not backend/ -
# add backend/ explicitly so `import app` resolves regardless of invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.location import Location  # noqa: E402
from app.models.tenant import Org  # noqa: E402
from app.models.user import User  # noqa: E402

DEV_EMAIL = "dev@evowerk.dev"
DEV_PASSWORD = "DevPassword123"  # pragma: allowlist secret


async def main() -> None:
    async with async_session_factory() as db:
        existing = await db.scalar(select(User).where(User.email == DEV_EMAIL))
        if existing:
            print(f"Dev user already exists: {DEV_EMAIL} / {DEV_PASSWORD}")
            return

        org = Org(name="Dev Org")
        db.add(org)
        await db.flush()
        db.add(Location(org_id=org.id, code="MAIN", name="Main", is_default=True))
        db.add(
            User(
                org_id=org.id,
                email=DEV_EMAIL,
                password_hash=hash_password(DEV_PASSWORD),
                role="owner",
            )
        )
        await db.commit()

    print(f"Dev user created: {DEV_EMAIL} / {DEV_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
