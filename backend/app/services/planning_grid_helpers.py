"""BOM-reachability and prospective-job reservation helpers for
compute_planning_grid. Split out of planning_grid.py, which re-exports both."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _reachable_components(
    root: uuid.UUID, children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]
) -> set[uuid.UUID]:
    """Every component below root in the active-BOM graph, at any depth."""
    seen: set[uuid.UUID] = set()
    stack = [child for child, _ in children.get(root, [])]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(child for child, _ in children.get(node, []))
    return seen


async def _reserved_against_a_new_job(db: AsyncSession, org_id: uuid.UUID, reserved_for) -> dict:
    """What existing scheduled jobs hold ahead of the job a Make press would
    raise: undated, at the default location, numbered next.

    Asking the real reservation rule about that hypothetical job is what keeps
    the button and the start endpoint agreeing. Reproducing the ranking here
    instead is how the grid would come to offer Make on a job that 409s.
    """
    from app.models.work_order import WorkOrder
    from app.services.locations import get_default_location_id

    next_number = (
        await db.scalar(select(func.max(WorkOrder.wo_number)).where(WorkOrder.org_id == org_id))
        or 0
    ) + 1
    prospective = WorkOrder(
        id=uuid.uuid4(),
        org_id=org_id,
        location_id=await get_default_location_id(db, org_id),
        scheduled_start=None,
        wo_number=next_number,
    )
    return await reserved_for(db, org_id, prospective)
