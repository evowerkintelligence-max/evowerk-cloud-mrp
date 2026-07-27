from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryBalance
from app.models.location import Location
from app.models.lot import Lot, LotTransaction
from app.models.work_order import WorkOrder, WorkOrderComponent, WorkOrderComponentLot
from app.services.inventory import apply_inventory_delta
from app.services.locations import location_code, resolve_location_id
from app.services.lots import consume_lot_fefo
from app.services.work_orders import (
    active_bom_id,
    blocking_job,
    component_requirements,
    reserved_by_higher_priority,
)

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

# Guards the wo_number max+1 read-then-insert race, same as so_number's twin
# in sales_orders.py: two concurrent creates in one org can read the same max
# and collide on uq_wos_org_number.
MAX_WO_NUMBER_ATTEMPTS = 3

# Numeric(12, 4) on WorkOrder.quantity_planned: 8 digits before the decimal
# point. Matches the sales-order and planning-grid write's own bound.
MAX_LINE_QUANTITY = 100_000_000


class WorkOrderCreateRequest(BaseModel):
    item_id: str
    quantity_planned: Decimal
    scheduled_start: date | None = None
    location_id: str | None = None
    notes: str | None = None
    # Raise and start in the one request, so the grid's Make button is one
    # click. Shares the create's transaction: a job that cannot be started is
    # not raised either.
    start_now: bool = False


class WorkOrderUpdateRequest(BaseModel):
    # Every field optional: a PATCH touches only what it sends. Omitted
    # fields keep the job's current value.
    quantity_planned: Decimal | None = None
    scheduled_start: date | None = None
    location_id: str | None = None
    notes: str | None = None


def _validate_quantity(quantity: Decimal) -> None:
    if quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
    # Numeric(12, 4) holds 8 integer digits. Postgres raises a numeric
    # overflow on anything larger, which would surface as a 500 rather than a
    # message the user can act on. Same bound sales_orders._validate_lines uses.
    if quantity >= MAX_LINE_QUANTITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that quantity is too large")


class WorkOrderComponentResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    quantity_required: Decimal
    quantity_consumed: Decimal
    sequence: int


class WorkOrderResponse(BaseModel):
    id: str
    wo_number: int
    item_id: str
    item_sku: str
    item_name: str
    status: str
    quantity_planned: Decimal
    quantity_good: Decimal
    quantity_scrapped: Decimal
    scrap_reason: str | None
    remainder_disposition: str | None
    location_id: str
    location_code: str
    scheduled_start: date | None
    started_at: str | None = None
    finished_at: str | None = None
    notes: str | None
    components: list[WorkOrderComponentResponse]


def _wo_to_response(
    wo: WorkOrder, item: Item, location: Location, components: list[WorkOrderComponentResponse]
) -> WorkOrderResponse:
    return WorkOrderResponse(
        id=str(wo.id),
        wo_number=wo.wo_number,
        item_id=str(wo.item_id),
        item_sku=item.sku,
        item_name=item.name,
        status=wo.status,
        quantity_planned=wo.quantity_planned,
        quantity_good=wo.quantity_good,
        quantity_scrapped=wo.quantity_scrapped,
        scrap_reason=wo.scrap_reason,
        remainder_disposition=wo.remainder_disposition,
        location_id=str(wo.location_id),
        location_code=location.code,
        scheduled_start=wo.scheduled_start,
        started_at=wo.started_at.isoformat() if wo.started_at else None,
        finished_at=wo.finished_at.isoformat() if wo.finished_at else None,
        notes=wo.notes,
        components=components,
    )


def _component_to_response(component: WorkOrderComponent, item: Item) -> WorkOrderComponentResponse:
    return WorkOrderComponentResponse(
        item_id=str(component.item_id),
        item_sku=item.sku,
        item_name=item.name,
        quantity_required=component.quantity_required,
        quantity_consumed=component.quantity_consumed,
        sequence=component.sequence,
    )


async def _fetch_components_for_orders(
    db: AsyncSession, org_id: uuid.UUID, wo_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[WorkOrderComponentResponse]]:
    """Every component for a page of jobs in one query, grouped by job."""
    if not wo_ids:
        return {}
    rows = (
        await db.execute(
            select(WorkOrderComponent, Item)
            .join(Item, Item.id == WorkOrderComponent.item_id)
            .where(WorkOrderComponent.org_id == org_id, WorkOrderComponent.wo_id.in_(wo_ids))
            .order_by(WorkOrderComponent.sequence)
        )
    ).all()
    grouped: dict[uuid.UUID, list[WorkOrderComponentResponse]] = defaultdict(list)
    for component, item in rows:
        grouped[component.wo_id].append(_component_to_response(component, item))
    return grouped


async def _detail_response(
    db: AsyncSession, wo: WorkOrder, item: Item, location: Location
) -> WorkOrderResponse:
    components = (await _fetch_components_for_orders(db, wo.org_id, [wo.id])).get(wo.id, [])
    return _wo_to_response(wo, item, location, components)


def _plain(quantity: Decimal) -> str:
    """20.0000 reads as 20 in a message a shop-floor user has to act on."""
    return format(quantity.normalize(), "f")


async def _lock_component_stock(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """On-hand at this location per item, from whichever subsystem holds it,
    with those rows locked for the rest of the transaction. Lot-tracked items
    count only active, non-depleted lots, the same stock consume_lot_fefo is
    willing to draw from, so the check and the consume agree.
    """
    balances = (
        await db.execute(
            select(InventoryBalance.item_id, InventoryBalance.quantity_on_hand)
            .where(
                InventoryBalance.org_id == org_id,
                InventoryBalance.location_id == location_id,
                InventoryBalance.item_id.in_(item_ids),
            )
            .order_by(InventoryBalance.item_id)
            .with_for_update()
        )
    ).all()
    lots = (
        await db.execute(
            select(Lot.item_id, Lot.quantity_remaining)
            .where(
                Lot.org_id == org_id,
                Lot.location_id == location_id,
                Lot.item_id.in_(item_ids),
                Lot.status == "active",
                Lot.quantity_remaining > 0,
            )
            # item_id leads so two jobs sharing components lock items in the
            # same order. Within an item the rest mirrors consume_lot_fefo's
            # iteration order, because that is the order every other lot
            # consumer takes these same row locks in; sorting by Lot.id here
            # instead would let a start and a concurrent FEFO consume grab two
            # lots in opposite orders and deadlock.
            .order_by(Lot.item_id, Lot.expiry_date.is_(None), Lot.expiry_date, Lot.created_at)
            .with_for_update()
        )
    ).all()
    on_hand: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for item_id, quantity in (*balances, *lots):
        on_hand[item_id] += quantity
    return on_hand


async def _start_job(db: AsyncSession, job: WorkOrder, current: CurrentUser) -> None:
    """Take the job's components off the shelf and mark it running.

    Writes, never commits: the caller owns the transaction, so a job short on
    its third component rolls back the first two rather than half-consuming
    itself. That is also why there is no pre-flight availability sweep here.
    """
    if job.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"job {job.wo_number} is {job.status}")

    rows = (
        await db.execute(
            select(WorkOrderComponent, Item)
            .join(Item, Item.id == WorkOrderComponent.item_id)
            .where(WorkOrderComponent.org_id == current.org_id, WorkOrderComponent.wo_id == job.id)
            # BOM order, so a refusal names the component a user would expect
            # to hit first. The deadlock-safe lock ordering lives entirely in
            # _lock_component_stock, so this is only the consume loop's order.
            .order_by(WorkOrderComponent.sequence)
        )
    ).all()

    # Lock the stock this start is about to read and write, in
    # _lock_component_stock's deterministic order, BEFORE computing
    # availability. Two jobs sharing components would otherwise both read the
    # same free quantity and both pass, and the loser would fail later with a
    # confusing shortfall instead of a reservation message. The deterministic
    # order is what stops two jobs locking the same pair in opposite orders
    # and deadlocking. The suite runs on SQLite where FOR UPDATE is a no-op,
    # so this is guaranteed by the query and by review, not by a concurrency
    # test.
    on_hand = await _lock_component_stock(
        db, current.org_id, job.location_id, [item.id for _, item in rows]
    )
    reserved = await reserved_by_higher_priority(db, current.org_id, job)

    moved: dict[str, str] = {}
    for component, item in rows:
        free = on_hand.get(item.id, Decimal(0)) - reserved.get(item.id, Decimal(0))
        if free < component.quantity_required:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                await _shortfall_detail(db, job, item, component.quantity_required - free, reserved),
            )

        # Two paths on purpose, do not collapse them. apply_inventory_delta
        # does not check for lot tracking, so sending a lot-tracked item
        # down it would write an InventoryBalance row alongside the lots and
        # double-count the stock. This branch IS the guard.
        if item.is_lot_tracked:
            consumptions = await consume_lot_fefo(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=job.location_id,
                quantity=component.quantity_required,
            )
            for entry in consumptions:
                db.add(
                    WorkOrderComponentLot(
                        org_id=current.org_id,
                        wo_component_id=component.id,
                        lot_id=entry.lot.id,
                        quantity=entry.quantity,
                    )
                )
                db.add(
                    LotTransaction(
                        org_id=current.org_id,
                        lot_id=entry.lot.id,
                        transaction_type="consumption",
                        quantity=entry.quantity,
                        reason="work order",
                        created_by=current.id,
                    )
                )
        else:
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=job.location_id,
                delta=-component.quantity_required,
                transaction_type="consumption",
                created_by=current.id,
                reason="work order",
            )
        component.quantity_consumed = component.quantity_required
        moved[item.sku] = str(component.quantity_required)

    job.status = "in_progress"
    job.started_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="work_order.started",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(job.id),
        metadata={"wo_number": job.wo_number, "moved": moved},
    )


async def _shortfall_detail(
    db: AsyncSession,
    job: WorkOrder,
    item: Item,
    shortfall: Decimal,
    reserved: dict[uuid.UUID, Decimal],
) -> str:
    """Both extra lookups live here, on the refusal path only, so the start
    that succeeds pays for neither."""
    # location_code falls back rather than raising, so a missing location
    # cannot turn this 409 into a 404 about the wrong thing.
    code = await location_code(db, job.location_id)
    detail = f"{item.sku}: {_plain(shortfall)} short at {code}"
    holder = await blocking_job(db, job.org_id, job, item.id) if reserved.get(item.id) else None
    if holder is not None:
        due = holder.scheduled_start.isoformat() if holder.scheduled_start else "now"
        detail += f", WO-{holder.wo_number} needs them on {due}"
    return detail


@router.post("", response_model=WorkOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: WorkOrderCreateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    _validate_quantity(body.quantity_planned)

    try:
        item_id = uuid.UUID(body.item_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid item_id") from None
    item = await db.scalar(select(Item).where(Item.org_id == current.org_id, Item.id == item_id))
    if item is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "item not found")

    bom_id = await active_bom_id(db, current.org_id, item_id)
    if bom_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.sku} has no active recipe")

    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    requirements = await component_requirements(db, current.org_id, item_id, body.quantity_planned)

    for attempt in range(MAX_WO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(WorkOrder.wo_number)).where(WorkOrder.org_id == current.org_id)
        )
        wo = WorkOrder(
            org_id=current.org_id,
            item_id=item_id,
            bom_id=bom_id,
            wo_number=(max_number or 0) + 1,
            status="scheduled",
            quantity_planned=body.quantity_planned,
            location_id=location_id,
            scheduled_start=body.scheduled_start,
            notes=body.notes,
            created_by=current.id,
        )
        # A per-attempt SAVEPOINT, not a full db.rollback(): a whole-transaction
        # rollback would expire the already-loaded `item`, so the `item.sku`
        # read while building the response would trigger an async refresh
        # mid-response (MissingGreenlet).
        try:
            async with db.begin_nested():
                db.add(wo)
                await db.flush()
            break
        except IntegrityError:
            if attempt == MAX_WO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "could not allocate a job number, please retry"
                ) from None

    for sequence, (component_item_id, quantity_required) in enumerate(requirements):
        db.add(
            WorkOrderComponent(
                org_id=current.org_id,
                wo_id=wo.id,
                item_id=component_item_id,
                quantity_required=quantity_required,
                sequence=sequence,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="work_order.raised",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={"wo_number": wo.wo_number, "item_id": str(item_id)},
    )
    if body.start_now:
        await _start_job(db, wo, current)
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.get("", response_model=list[WorkOrderResponse])
async def list_work_orders(
    q: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(WorkOrder, Item, Location)
        .join(Item, Item.id == WorkOrder.item_id)
        .join(Location, Location.id == WorkOrder.location_id)
        .where(WorkOrder.org_id == current.org_id)
    )
    if q:
        query = query.where(
            or_(Item.sku.ilike(f"%{q}%"), cast(WorkOrder.wo_number, String).ilike(f"%{q}%"))
        )
    if status_filter:
        query = query.where(WorkOrder.status == status_filter)
    query = query.order_by(WorkOrder.wo_number).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()

    # The list has to show what each job needs, so components ship with it.
    # One grouped query for the whole page rather than one per row: the panel
    # renders up to `limit` jobs, and a per-row fetch made that many round
    # trips (the defect caught in review on the sales-orders phase).
    components_by_wo = await _fetch_components_for_orders(
        db, current.org_id, [wo.id for wo, _, _ in rows]
    )
    return [
        _wo_to_response(wo, item, location, components_by_wo.get(wo.id, []))
        for wo, item, location in rows
    ]


@router.get("/{wo_id}", response_model=WorkOrderResponse)
async def get_work_order(
    wo_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wo = await get_or_404(db, WorkOrder, current.org_id, wo_id, "Work order not found")
    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.post("/{wo_id}/start", response_model=WorkOrderResponse)
async def start_work_order(
    wo_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Begin the job: its components leave stock now, so what the shop is
    holding and what the system says match while the job is running."""
    # Locked, not just read: two concurrent starts would otherwise both see
    # `scheduled` and both consume.
    wo = await get_or_404(
        db, WorkOrder, current.org_id, wo_id, "Work order not found", for_update=True
    )
    await _start_job(db, wo, current)
    await db.commit()

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)


@router.patch("/{wo_id}", response_model=WorkOrderResponse)
async def update_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderUpdateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    wo = await get_or_404(db, WorkOrder, current.org_id, wo_id, "Work order not found")
    if wo.status != "scheduled":
        # A started job's components are already being consumed on the floor;
        # editing the recipe or quantity under it would rewrite what already
        # happened.
        raise HTTPException(status.HTTP_409_CONFLICT, f"job {wo.wo_number} is {wo.status}")

    quantity_planned = wo.quantity_planned
    if body.quantity_planned is not None:
        _validate_quantity(body.quantity_planned)
        # Match the Numeric(12, 4) column so the response reflects what gets
        # stored, not the raw decimal places the caller happened to send.
        quantity_planned = body.quantity_planned.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    item = await get_or_404(db, Item, current.org_id, wo.item_id, "Item not found")
    # A job reflects the recipe as of its LAST edit, not the one it was
    # raised on: if the BOM was wrong and has since been fixed, editing
    # picks the fix up.
    bom_id = await active_bom_id(db, current.org_id, wo.item_id)
    if bom_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.sku} has no active recipe")

    if body.location_id is not None:
        wo.location_id = await resolve_location_id(db, current.org_id, body.location_id)
    if body.scheduled_start is not None:
        wo.scheduled_start = body.scheduled_start
    if body.notes is not None:
        wo.notes = body.notes
    wo.quantity_planned = quantity_planned
    wo.bom_id = bom_id

    # Delete and re-insert every time, even when only the date changed: it is
    # one query either way, and a conditional here is exactly how the stored
    # components and the recipe drift apart.
    requirements = await component_requirements(db, current.org_id, wo.item_id, quantity_planned)
    await db.execute(
        WorkOrderComponent.__table__.delete().where(WorkOrderComponent.wo_id == wo.id)
    )
    for sequence, (component_item_id, quantity_required) in enumerate(requirements):
        db.add(
            WorkOrderComponent(
                org_id=current.org_id,
                wo_id=wo.id,
                item_id=component_item_id,
                quantity_required=quantity_required,
                sequence=sequence,
            )
        )

    # Child rows changing alone never fires onupdate on the parent; touch it
    # so updated_at still means "last changed".
    wo.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="work_order.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(wo.id),
        metadata={"wo_number": wo.wo_number},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, wo.location_id, "Location not found")
    return await _detail_response(db, wo, item, location)
