from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.staff import StaffAccount


async def create_staff_account(db: AsyncSession, *, employee_id: str, password: str) -> StaffAccount:
    staff = StaffAccount(employee_id=employee_id, hashed_password=hash_password(password))
    db.add(staff)
    await db.commit()
    return staff


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed one EvoWerk staff account.")
    parser.add_argument("employee_id")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords did not match.")

    async with async_session_factory() as db:
        await create_staff_account(db, employee_id=args.employee_id, password=password)
    print(f"Created staff account for {args.employee_id!r}.")


if __name__ == "__main__":
    asyncio.run(_main())
