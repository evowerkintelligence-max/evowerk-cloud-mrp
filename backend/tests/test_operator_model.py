from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.operator import Operator

pytestmark = pytest.mark.asyncio


async def test_duplicate_normalized_name_same_org_rejected(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="J Kim", normalized_name="j kim"))
    await db_session.flush()
    db_session.add(Operator(org_id=org_id, name="j kim", normalized_name="j kim"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_normalized_name_different_org_allowed(db_session):
    db_session.add(Operator(org_id=uuid.uuid4(), name="J Kim", normalized_name="j kim"))
    db_session.add(Operator(org_id=uuid.uuid4(), name="J Kim", normalized_name="j kim"))
    await db_session.flush()  # no error


async def test_duplicate_badge_id_same_org_rejected(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="A", normalized_name="a", badge_id="B1"))
    await db_session.flush()
    db_session.add(Operator(org_id=org_id, name="B", normalized_name="b", badge_id="B1"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_badge_id_different_org_allowed(db_session):
    db_session.add(Operator(org_id=uuid.uuid4(), name="A", normalized_name="a", badge_id="B1"))
    db_session.add(Operator(org_id=uuid.uuid4(), name="B", normalized_name="b", badge_id="B1"))
    await db_session.flush()  # no error


async def test_multiple_null_badge_ids_same_org_allowed(db_session):
    org_id = uuid.uuid4()
    db_session.add(Operator(org_id=org_id, name="A", normalized_name="a"))
    db_session.add(Operator(org_id=org_id, name="B", normalized_name="b"))
    await db_session.flush()  # no error - the partial index only guards non-null badge_id
