# backend/tests/test_staff_session.py
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_staff_access_token, decode_token


def test_staff_access_token_has_staff_type_and_correct_subject():
    staff_id = str(uuid.uuid4())
    token = create_staff_access_token(staff_id=staff_id)

    payload = decode_token(token)
    assert payload["type"] == "staff_access"
    assert payload["sub"] == staff_id


@pytest.mark.asyncio
async def test_get_current_staff_rejects_a_customer_access_token(client, db_session):
    from app.models.staff import StaffAccount
    from app.core.security import hash_password
    from app.api.deps import get_current_staff
    from app.core.config import get_settings
    import jwt as pyjwt

    staff = StaffAccount(employee_id="e1", hashed_password=hash_password("x"))
    db_session.add(staff)
    await db_session.commit()

    settings = get_settings()
    # A well-formed customer "access" token, not a "staff_access" one.
    customer_token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "type": "access"},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(Exception):
        await get_current_staff(token=customer_token, db=db_session)
