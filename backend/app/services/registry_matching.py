"""Shared free-text -> registry-row matching, used by the one-time backfill
script (scripts/backfill_machine_registry.py) and by MaintenanceSchedule's
programmatic creation path (predictive.py), which has no human picking from
a dropdown - the machine name comes from whatever a user's dataset called
it. DowntimeTracking/PaceTracking retrofit to explicit dropdown selection
instead and don't need this - see the design spec's Frontend section."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.machine import Machine
from app.models.operator import Operator

FUZZY_MATCH_THRESHOLD = 0.8


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def normalized_similarity(a: str, b: str) -> float:
    """1.0 - normalized edit distance, after trim + casefold. 1.0 = identical."""
    na, nb = a.strip().casefold(), b.strip().casefold()
    longest = max(len(na), len(nb))
    if longest == 0:
        return 1.0
    return 1 - (_levenshtein(na, nb) / longest)


async def resolve_or_create_machine(
    db: AsyncSession, org_id: uuid.UUID, raw_name: str, default_work_center_id: uuid.UUID
) -> Machine:
    """Exact match (trim+casefold) on an existing Machine.name -> reuse it.
    Otherwise create a new Machine under default_work_center_id, flagged
    needs_review=True only when it's a fuzzy-close near-duplicate of an
    existing name (a likely typo, worth a human glance); a genuinely novel
    name is created without the flag."""
    existing = (await db.execute(select(Machine).where(Machine.org_id == org_id))).scalars().all()
    target = raw_name.strip()
    for m in existing:
        if m.name.strip().casefold() == target.casefold():
            return m

    needs_review = any(normalized_similarity(target, m.name) >= FUZZY_MATCH_THRESHOLD for m in existing)
    code = target.upper().replace(" ", "-")[:50]
    suffix = 1
    existing_codes = {m.code for m in existing}
    base_code = code
    while code in existing_codes:
        suffix += 1
        code = f"{base_code[:45]}-{suffix}"

    machine = Machine(
        org_id=org_id, work_center_id=default_work_center_id, code=code, name=target, needs_review=needs_review,
    )
    db.add(machine)
    await db.flush()
    return machine


async def resolve_or_create_operator(db: AsyncSession, org_id: uuid.UUID, raw_name: str) -> Operator:
    """Exact match on (org_id, normalized_name) - one indexed lookup, the
    overwhelmingly common path. On a miss, create a new row, flagged
    needs_review=True only when it's a fuzzy-close near-duplicate of an
    existing operator's name (a likely typo, worth a human glance)."""
    target = raw_name.strip()
    normalized = target.casefold()

    existing_match = (
        await db.execute(
            select(Operator).where(Operator.org_id == org_id, Operator.normalized_name == normalized)
        )
    ).scalar_one_or_none()
    if existing_match is not None:
        return existing_match

    others = (await db.execute(select(Operator).where(Operator.org_id == org_id))).scalars().all()
    needs_review = any(normalized_similarity(target, o.name) >= FUZZY_MATCH_THRESHOLD for o in others)

    operator = Operator(org_id=org_id, name=target, normalized_name=normalized, needs_review=needs_review)
    try:
        # Savepoint, not a bare db.flush()/db.rollback(): a session-wide
        # rollback would throw away the caller's pending work and expire the
        # objects it already loaded, so the next attribute touch would try
        # implicit IO mid-response (MissingGreenlet) - the same trap
        # purchase_orders.py's submit path documents. This rolls back only the
        # failed insert, matching lots.find_or_create_lot.
        async with db.begin_nested():
            db.add(operator)
            await db.flush()
    except IntegrityError:
        # Lost the create race to a concurrent scan for the same name (this is
        # a public, unauthenticated endpoint, so two badge readers can hit it
        # at once) - the other insert won, so resolve to its row instead of
        # 500ing. uq_operators_org_normalized_name is what fires here, so the
        # winning row is guaranteed to be findable by the same key.
        existing_match = (
            await db.execute(
                select(Operator).where(Operator.org_id == org_id, Operator.normalized_name == normalized)
            )
        ).scalar_one_or_none()
        if existing_match is None:
            raise
        return existing_match
    return operator
