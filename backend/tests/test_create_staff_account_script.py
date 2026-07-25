from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.staff import StaffAccount
from scripts.create_staff_account import create_staff_account

pytestmark = pytest.mark.asyncio


async def test_create_staff_account_inserts_a_row_with_a_verifiable_password(db_session):
    await create_staff_account(db_session, employee_id="new.hire", password="correct-horse-battery-staple1")

    row = (
        await db_session.execute(select(StaffAccount).where(StaffAccount.employee_id == "new.hire"))
    ).scalar_one()
    assert verify_password("correct-horse-battery-staple1", row.hashed_password)
