"""Shared free-text -> registry-row matching, used by the one-time backfill
script (scripts/backfill_machine_registry.py) and by MaintenanceSchedule's
programmatic creation path (predictive.py), which has no human picking from
a dropdown - the machine name comes from whatever a user's dataset called
it. DowntimeTracking/PaceTracking retrofit to explicit dropdown selection
instead and don't need this - see the design spec's Frontend section."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.machine import Machine

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
