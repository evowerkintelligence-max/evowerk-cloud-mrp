from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.staff import StaffAccount
from scripts.create_staff_account import create_staff_account

pytestmark = pytest.mark.asyncio


async def test_create_staff_account_inserts_a_row_with_a_verifiable_password(db_session):
    await create_staff_account(db_session, employee_id="new.hire", password=TEST_PASSWORD)

    row = (
        await db_session.execute(select(StaffAccount).where(StaffAccount.employee_id == "new.hire"))
    ).scalar_one()
    assert verify_password(TEST_PASSWORD, row.hashed_password)
