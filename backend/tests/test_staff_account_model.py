from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.staff import StaffAccount
from tests._test_secrets import WRONG_PASSWORD

pytestmark = pytest.mark.asyncio


async def test_staff_account_round_trips_through_the_db(db_session):
    staff = StaffAccount(employee_id="zeeshan", hashed_password=WRONG_PASSWORD)
    db_session.add(staff)
    await db_session.commit()

    fetched = (
        await db_session.execute(select(StaffAccount).where(StaffAccount.employee_id == "zeeshan"))
    ).scalar_one()
    assert fetched.id is not None
    assert isinstance(fetched.id, uuid.UUID)
    assert fetched.hashed_password == WRONG_PASSWORD
    assert fetched.created_at is not None
