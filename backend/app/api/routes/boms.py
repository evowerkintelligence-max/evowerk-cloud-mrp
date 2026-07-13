from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.core.time import ensure_utc
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Bom, BomLine, Item, ItemUnit

router = APIRouter(prefix="/boms", tags=["boms"])

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
    instead of a raw 500.
    """
    try:
        await db.flush()
    except StaleDataError:
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
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")

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
    bom = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    item = await get_or_404(db, Item, current.org_id, bom.item_id, "Item not found")

    lines_result = await db.execute(
        select(BomLine, Item, ItemUnit)
        .join(Item, Item.id == BomLine.component_item_id)
        .outerjoin(ItemUnit, ItemUnit.id == BomLine.unit_id)
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
            quantity=line.quantity.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            sequence=line.sequence,
            scrap_pct=line.scrap_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            unit_id=str(line.unit_id) if line.unit_id else None,
            unit_quantity=line.unit_quantity.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if line.unit_quantity else None,
            unit_code=unit.code if unit else None,
        )
        for line, component, unit in lines_result.all()
    ]

    base = await _bom_to_response(db, bom, item)
    return BomDetailResponse(**base.model_dump(), lines=lines)


@router.patch("/{bom_id}", response_model=BomDetailResponse)
async def update_bom_lines(
    bom_id: uuid.UUID,
    body: BomLinesUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    lines_in = body.lines
    bom = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be edited")
    if ensure_utc(bom.updated_at) != ensure_utc(body.expected_updated_at):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This BOM was changed elsewhere since you loaded it - refresh and try again",
        )
    if not lines_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A BOM needs at least one line")

    component_ids: set[uuid.UUID] = set()
    unit_ids: set[uuid.UUID] = set()
    parsed: list[tuple[BomLineInput, uuid.UUID, uuid.UUID | None]] = []
    for line_in in lines_in:
        if line_in.scrap_pct < 0 or line_in.scrap_pct > MAX_SCRAP_PCT:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scrap_pct must be between 0 and {MAX_SCRAP_PCT}")
        try:
            component_id = uuid.UUID(line_in.component_item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid component_item_id") from None
        if component_id == bom.item_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "A BOM can't list its own item as a component")
        component_ids.add(component_id)

        unit_uuid: uuid.UUID | None = None
        if line_in.unit_id is not None:
            if line_in.unit_quantity is None or line_in.unit_quantity <= 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "unit_quantity must be positive when unit_id is set"
                )
            try:
                unit_uuid = uuid.UUID(line_in.unit_id)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid unit_id") from None
            unit_ids.add(unit_uuid)
        parsed.append((line_in, component_id, unit_uuid))

    # One query for every distinct component instead of one per line - a BOM
    # with dozens of lines was otherwise issuing that many item lookups.
    found_result = await db.execute(
        select(Item.id).where(Item.org_id == current.org_id, Item.id.in_(component_ids))
    )
    if {row[0] for row in found_result.all()} != component_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

    units_by_id: dict[uuid.UUID, ItemUnit] = {}
    if unit_ids:
        units_result = await db.execute(
            select(ItemUnit).where(ItemUnit.org_id == current.org_id, ItemUnit.id.in_(unit_ids))
        )
        units_by_id = {u.id: u for u in units_result.scalars().all()}
        if set(units_by_id) != unit_ids:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found")

    # Same check activate() runs, surfaced here so a cycle is caught the moment
    # it's drafted rather than after the user has finished building the BOM.
    # activate() re-checks anyway, since the active-BOM graph can shift between
    # this save and that activation.
    await _reject_if_cycle(db, current.org_id, bom.item_id, component_ids)

    final_lines: list[BomLine] = []
    for line_in, component_id, unit_uuid in parsed:
        if unit_uuid is not None:
            unit = units_by_id[unit_uuid]
            if unit.item_id != component_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Unit does not belong to this line's component item"
                )
            quantity = (line_in.unit_quantity * unit.factor_to_base).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        else:
            quantity = line_in.quantity
        if quantity <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
        if quantity > MAX_LINE_QUANTITY:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"quantity must not exceed {MAX_LINE_QUANTITY}")
        final_lines.append(
            BomLine(
                org_id=current.org_id,
                bom_id=bom.id,
                component_item_id=component_id,
                quantity=quantity,
                sequence=line_in.sequence,
                scrap_pct=line_in.scrap_pct,
                unit_id=unit_uuid,
                unit_quantity=(
                    line_in.unit_quantity.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    if unit_uuid is not None
                    else None
                ),
            )
        )

    await db.execute(BomLine.__table__.delete().where(BomLine.bom_id == bom.id))
    for line in final_lines:
        db.add(line)
    # bom_lines is a separate table, so nothing here dirties the boms row on
    # its own - bump updated_at explicitly or the CAS check above would never
    # actually detect this edit on a subsequent PATCH.
    bom.updated_at = datetime.now(timezone.utc)
    await _flush_checking_version(db)

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
    bom = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be activated")

    # Lock every BOM row in the org for the rest of this transaction so two
    # concurrent activations can't both walk the cycle check before either
    # commits - e.g. mutually-referencing drafts A and B could otherwise each
    # see the other as still-draft (invisible to the active-only walk) and
    # both pass, creating a real cycle once both commit. A second activate()
    # in this org now blocks here until the first one commits or rolls back.
    await db.execute(select(Bom.id).where(Bom.org_id == current.org_id).with_for_update())

    lines_result = await db.execute(select(BomLine.component_item_id).where(BomLine.bom_id == bom.id))
    component_ids = {row[0] for row in lines_result.all()}
    if not component_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "Can't activate a BOM with no lines")

    await _reject_if_cycle(db, current.org_id, bom.item_id, component_ids)

    previously_active = await db.execute(
        select(Bom).where(Bom.org_id == current.org_id, Bom.item_id == bom.item_id, Bom.status == "active")
    )
    for other in previously_active.scalars().all():
        other.status = "archived"

    bom.status = "active"
    await _flush_checking_version(db)

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


@router.post("/{bom_id}/revise", response_model=BomDetailResponse, status_code=status.HTTP_201_CREATED)
async def revise_bom(
    bom_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Clone an active BOM's lines into a new draft version, so revising a
    recipe doesn't mean re-entering every line from scratch. The source BOM
    stays active and untouched - a past production run still points at
    exactly the recipe it used. Same version allocation as create_bom."""
    source = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if source.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active BOM can be revised")

    source_lines_result = await db.execute(
        select(BomLine).where(BomLine.bom_id == source.id).order_by(BomLine.sequence)
    )
    source_lines = source_lines_result.scalars().all()

    for attempt in range(MAX_VERSION_ATTEMPTS):
        max_version = await db.scalar(
            select(func.max(Bom.version)).where(Bom.org_id == current.org_id, Bom.item_id == source.item_id)
        )
        new_bom = Bom(
            org_id=current.org_id,
            item_id=source.item_id,
            version=(max_version or 0) + 1,
            status="draft",
            created_by=current.id,
        )
        db.add(new_bom)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == MAX_VERSION_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a BOM version, please retry"
                ) from None

    for line in source_lines:
        db.add(
            BomLine(
                org_id=current.org_id,
                bom_id=new_bom.id,
                component_item_id=line.component_item_id,
                quantity=line.quantity,
                sequence=line.sequence,
                scrap_pct=line.scrap_pct,
                unit_id=line.unit_id,
                unit_quantity=line.unit_quantity,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="bom.revised",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(new_bom.id),
        metadata={"source_bom_id": str(source.id), "item_id": str(source.item_id), "version": new_bom.version},
    )
    await db.commit()
    return await get_bom(new_bom.id, current, db)


@router.delete("/{bom_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bom(
    bom_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    bom = await get_or_404(db, Bom, current.org_id, bom_id, "BOM not found")
    if bom.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft BOMs can be deleted")

    # Guarded DELETE, not delete-by-id: a concurrent activate() could flip this
    # BOM to active between the check above and here. Re-check status='draft'
    # as part of the DELETE itself so that race loses cleanly instead of
    # silently deleting a BOM another request just made active.
    result = await db.execute(Bom.__table__.delete().where(Bom.id == bom.id, Bom.status == "draft"))
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This BOM was changed elsewhere since you loaded it - refresh and try again",
        )
    await log_event(
        db,
        action="bom.deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(bom.id),
        metadata={"item_id": str(bom.item_id), "version": bom.version},
    )
    await db.commit()
