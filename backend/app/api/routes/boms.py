from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Bom, BomLine, Item

router = APIRouter(prefix="/boms", tags=["boms"])

# Guards the version max+1 read-then-insert race: two concurrent creates for
# the same item can both read the same max and collide on
# uq_boms_org_item_version. Retry a few times rather than surfacing an
# unhandled 500 (found during the procurement feature's design review, which
# uses the identical pattern for po_number - see create_purchase_order).
MAX_VERSION_ATTEMPTS = 3

# Only activate() walks this graph, since a draft's lines don't affect any live
# calculation - the only moment a new "produces" edge becomes real is
# activation. Bounded rather than an unbounded graph walk, since this runs
# synchronously inside a request.
MAX_CYCLE_CHECK_DEPTH = 20


class BomLineInput(BaseModel):
    component_item_id: str
    quantity: Decimal
    sequence: int = 0


class BomLineResponse(BaseModel):
    id: str
    component_item_id: str
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity: Decimal
    sequence: int


class BomCreateRequest(BaseModel):
    item_id: str


class BomResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    version: int
    status: str


class BomDetailResponse(BomResponse):
    lines: list[BomLineResponse]


async def _get_item_or_404(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    result = await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


async def _get_bom_or_404(db: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID) -> Bom:
    result = await db.execute(select(Bom).where(Bom.id == bom_id, Bom.org_id == org_id))
    bom = result.scalar_one_or_none()
    if bom is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BOM not found")
    return bom


async def _bom_to_response(db: AsyncSession, bom: Bom, item: Item | None = None) -> BomResponse:
    if item is None:
        item = await _get_item_or_404(db, bom.org_id, bom.item_id)
    return BomResponse(
        id=str(bom.id), item_id=str(bom.item_id), item_sku=item.sku, item_name=item.name,
        version=bom.version, status=bom.status,
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

    for _ in range(MAX_CYCLE_CHECK_DEPTH):
        if not frontier:
            return False
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


@router.post("", response_model=BomResponse, status_code=status.HTTP_201_CREATED)
async def create_bom(
    body: BomCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item_id = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item_id") from None
    item = await _get_item_or_404(db, current.org_id, item_id)

    for attempt in range(MAX_VERSION_ATTEMPTS):
        max_version = await db.scalar(
            select(func.max(Bom.version)).where(Bom.org_id == current.org_id, Bom.item_id == item_id)
        )
        bom = Bom(
            org_id=current.org_id,
            item_id=item_id,
            version=(max_version or 0) + 1,
            status="draft",
            created_by=current.id,
        )
        db.add(bom)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == MAX_VERSION_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a BOM version, please retry"
                ) from None

    await log_event(
        db,
        action="bom.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(bom.id),
        metadata={"item_id": str(item_id), "version": bom.version},
    )
    await db.commit()
    return await _bom_to_response(db, bom, item)


@router.get("", response_model=list[BomResponse])
async def list_boms(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bom, Item)
        .join(Item, Item.id == Bom.item_id)
        .where(Bom.org_id == current.org_id)
        .order_by(Item.sku, Bom.version)
    )
    return [await _bom_to_response(db, bom, item) for bom, item in result.all()]


@router.get("/{bom_id}", response_model=BomDetailResponse)
async def get_bom(
    bom_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bom = await _get_bom_or_404(db, current.org_id, bom_id)
    item = await _get_item_or_404(db, current.org_id, bom.item_id)

    lines_result = await db.execute(
        select(BomLine, Item)
        .join(Item, Item.id == BomLine.component_item_id)
        .where(BomLine.bom_id == bom.id)
        .order_by(BomLine.sequence)
    )
    lines = [
        BomLineResponse(
            id=str(line.id),
            component_item_id=str(line.component_item_id),
            component_sku=component.sku,
            component_name=component.name,
            unit_of_measure=component.unit_of_measure,
            quantity=line.quantity,
            sequence=line.sequence,
        )
        for line, component in lines_result.all()
    ]

    base = await _bom_to_response(db, bom, item)
    return BomDetailResponse(**base.model_dump(), lines=lines)


@router.patch("/{bom_id}", response_model=BomDetailResponse)
async def update_bom_lines(
    bom_id: uuid.UUID,
    lines_in: list[BomLineInput],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    bom = await _get_bom_or_404(db, current.org_id, bom_id)
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be edited")
    if not lines_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A BOM needs at least one line")

    for line_in in lines_in:
        if line_in.quantity <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
        try:
            component_id = uuid.UUID(line_in.component_item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid component_item_id") from None
        if component_id == bom.item_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "A BOM can't list its own item as a component")
        await _get_item_or_404(db, current.org_id, component_id)

    await db.execute(BomLine.__table__.delete().where(BomLine.bom_id == bom.id))
    for line_in in lines_in:
        db.add(
            BomLine(
                org_id=current.org_id,
                bom_id=bom.id,
                component_item_id=uuid.UUID(line_in.component_item_id),
                quantity=line_in.quantity,
                sequence=line_in.sequence,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="bom.lines_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(bom.id),
        metadata={"line_count": len(lines_in)},
    )
    await db.commit()
    return await get_bom(bom_id, current, db)


@router.post("/{bom_id}/activate", response_model=BomResponse)
async def activate_bom(
    bom_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    bom = await _get_bom_or_404(db, current.org_id, bom_id)
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be activated")

    lines_result = await db.execute(select(BomLine.component_item_id).where(BomLine.bom_id == bom.id))
    component_ids = {row[0] for row in lines_result.all()}
    if not component_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "Can't activate a BOM with no lines")

    if await _would_create_cycle(db, current.org_id, bom.item_id, component_ids):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Activating this BOM would create a circular dependency (a component's "
            "own BOM eventually requires this item)",
        )

    previously_active = await db.execute(
        select(Bom).where(Bom.org_id == current.org_id, Bom.item_id == bom.item_id, Bom.status == "active")
    )
    for other in previously_active.scalars().all():
        other.status = "archived"

    bom.status = "active"
    await db.flush()

    await log_event(
        db,
        action="bom.activated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(bom.id),
        metadata={"item_id": str(bom.item_id), "version": bom.version},
    )
    await db.commit()
    return await _bom_to_response(db, bom)
