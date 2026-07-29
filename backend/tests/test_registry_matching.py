from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.operator import Operator
from app.services.registry_matching import normalized_similarity, resolve_or_create_operator

pytestmark = pytest.mark.asyncio


def test_normalized_similarity_exact_after_casefold_and_trim():
    assert normalized_similarity(" CNC-01 ", "cnc-01") == 1.0


def test_normalized_similarity_near_duplicate_above_threshold():
    assert normalized_similarity("CNC-01", "CNC01") >= 0.8


def test_normalized_similarity_unrelated_below_threshold():
    assert normalized_similarity("CNC-01", "Assembly Line 3") < 0.8


async def test_resolve_or_create_operator_exact_match_reuses_row(db_session):
    org_id = uuid.uuid4()
    first = await resolve_or_create_operator(db_session, org_id, "J Kim")
    await db_session.flush()

    second = await resolve_or_create_operator(db_session, org_id, "  j kim ")
    await db_session.flush()

    assert second.id == first.id
    count = (
        await db_session.execute(select(Operator).where(Operator.org_id == org_id))
    ).scalars().all()
    assert len(count) == 1


async def test_resolve_or_create_operator_near_duplicate_flags_needs_review(db_session):
    org_id = uuid.uuid4()
    await resolve_or_create_operator(db_session, org_id, "Johnson")
    await db_session.flush()

    close = await resolve_or_create_operator(db_session, org_id, "Jonson")
    await db_session.flush()

    assert close.needs_review is True


async def test_resolve_or_create_operator_unrelated_name_not_flagged(db_session):
    org_id = uuid.uuid4()
    await resolve_or_create_operator(db_session, org_id, "Kim")
    await db_session.flush()

    unrelated = await resolve_or_create_operator(db_session, org_id, "Alex Rodriguez")
    await db_session.flush()

    assert unrelated.needs_review is False


async def test_resolve_or_create_operator_scoped_per_org(db_session):
    name = "J Kim"
    a = await resolve_or_create_operator(db_session, uuid.uuid4(), name)
    b = await resolve_or_create_operator(db_session, uuid.uuid4(), name)
    await db_session.flush()

    assert a.id != b.id
