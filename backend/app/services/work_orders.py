from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom
from app.services.mrp import load_bom_children


async def active_bom_id(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> uuid.UUID | None:
    """The item's currently active recipe, or None if it has none. A job
    snapshots this so a later BOM edit cannot rewrite what it committed to."""
    return await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )


async def component_requirements(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> list[tuple[uuid.UUID, Decimal]]:
    """What a job for `quantity` of `item_id` needs, one entry per direct
    component.

    Deliberately ONE level deep, not a full explosion: a work order consumes
    what its own recipe lists. If a sub-assembly is itself manufactured, it
    is consumed as a part and gets its own job; exploding through it here
    would consume raw material the shop never touched for this job.

    load_bom_children already folds each line's scrap_pct into the per-unit
    figure, so this must not apply scrap a second time.
    """
    children = await load_bom_children(db, org_id)
    return [(child_id, qty_per * quantity) for child_id, qty_per in children.get(item_id, [])]
