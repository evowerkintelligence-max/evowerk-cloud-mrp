"""Schemas and helpers for the /boms routes (split out of boms.py)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import get_or_404
from app.models.bom import Bom, BomLine, Item


# Guards the version max+1 read-then-insert race: two concurrent creates for
# the same item can both read the same max and collide on
# uq_boms_org_item_version. Retry a few times rather than surfacing an
# unhandled 500 (found during the procurement feature's design review, which
# uses the identical pattern for po_number - see create_purchase_order).
MAX_VERSION_ATTEMPTS = 3

# BomLine.quantity is NUMERIC(12,4) - 8 integer digits max. Postgres raises an
# unhandled numeric-overflow error on insert past this (uncaught here - only
# StaleDataError is - and sqlite's test backend doesn't enforce it at all, so
# nothing else would catch this), so reject it explicitly with a clean 400.
MAX_LINE_QUANTITY = Decimal("99999999.9999")

# scrap_pct is NUMERIC(5,2) - matches the 0-100 range enforced below.
MAX_SCRAP_PCT = Decimal("100")


class BomLineInput(BaseModel):
    component_item_id: str
    quantity: Decimal
    sequence: int = 0
    # User-set expected material loss for this component, as a percent - see
    # BomLine.scrap_pct for how MRP applies it.
    scrap_pct: Decimal = Decimal(0)
    # Optional: author this line in a unit other than the component's base
    # unit. Both null means "quantity is already in the base unit" - the
    # server ignores `quantity` and recomputes it from unit_quantity *
    # the unit's factor_to_base whenever unit_id is set.
    unit_id: str | None = None
    unit_quantity: Decimal | None = None


class BomLinesUpdateRequest(BaseModel):
    lines: list[BomLineInput]
    # Echoed back from the last BomResponse the client saw. Guards against two
    # editors overwriting each other's lines: the second PATCH to land loses
    # the race and must refetch and retry rather than silently clobbering the
    # first editor's lines with a stale full-snapshot replace.
    expected_updated_at: datetime


class BomLineResponse(BaseModel):
    id: str
    component_item_id: str
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity: Decimal
    sequence: int
    scrap_pct: Decimal
    unit_id: str | None
    unit_quantity: Decimal | None
    unit_code: str | None


class BomCreateRequest(BaseModel):
    item_id: str


class BomResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    version: int
    status: str
    updated_at: datetime


class BomDetailResponse(BomResponse):
    lines: list[BomLineResponse]


async def _reject_if_cycle(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, component_ids: set[uuid.UUID]
) -> None:
    if await _would_create_cycle(db, org_id, item_id, component_ids):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This would create a circular dependency (a component's own active BOM "
            "eventually requires this item)",
        )


async def _flush_checking_version(db: AsyncSession) -> None:
    """The expected_updated_at / status pre-checks in each route catch the
    common case with a clear message; row_version (the Bom model's
    version_id_col) is the real guard for the narrow window between that
    check and this flush - convert its StaleDataError into the same 409
    instead of a raw 500. Also converts a hit on uq_boms_org_item_active (the
    DB-level one-active-BOM-per-item backstop - see app/models/bom.py) the
    same way, since that's reachable from activate_bom's app-level checks
    ever getting out of sync with the DB the same way a version conflict is.
    """
    try:
        await db.flush()
    except StaleDataError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This BOM was changed elsewhere since you loaded it - refresh and try again",
        ) from None
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This BOM was changed elsewhere since you loaded it - refresh and try again",
        ) from None


async def _bom_to_response(db: AsyncSession, bom: Bom, item: Item | None = None) -> BomResponse:
    if item is None:
        item = await get_or_404(db, Item, bom.org_id, bom.item_id, "Item not found")
    return BomResponse(
        id=str(bom.id), item_id=str(bom.item_id), item_sku=item.sku, item_name=item.name,
        version=bom.version, status=bom.status, updated_at=bom.updated_at,
    )


async def _would_create_cycle(
    db: AsyncSession, org_id: uuid.UUID, target_item_id: uuid.UUID, start_component_ids: set[uuid.UUID]
) -> bool:
    """Walks the graph of currently-active BOMs only, starting from a
    candidate BOM's line components: does any of them (transitively, via its
    own active BOM's components) resolve back to target_item_id? That would
    mean target_item_id indirectly requires itself to be built.
    """
    visited: set[uuid.UUID] = set()
    frontier = start_component_ids

    # No fixed depth cap: each pass strictly grows `visited` (frontier -=
    # visited, then folds the remainder in) or returns, and visited can never
    # exceed the org's total item count - so this always terminates without
    # needing an arbitrary ceiling that would false-positive on a deep-but-
    # acyclic BOM tree.
    while frontier:
        if target_item_id in frontier:
            return True
        frontier -= visited
        if not frontier:
            return False
        visited |= frontier

        result = await db.execute(
            select(Bom.id).where(Bom.org_id == org_id, Bom.status == "active", Bom.item_id.in_(frontier))
        )
        active_bom_ids = [row[0] for row in result.all()]
        if not active_bom_ids:
            return False

        lines_result = await db.execute(
            select(BomLine.component_item_id).where(BomLine.bom_id.in_(active_bom_ids))
        )
        frontier = {row[0] for row in lines_result.all()}

    return False
