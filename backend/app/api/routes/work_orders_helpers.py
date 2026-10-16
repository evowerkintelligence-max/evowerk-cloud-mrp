"""Schemas and helpers for the /work-orders routes (split out of work_orders.py)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryBalance
from app.models.location import Location
from app.models.lot import Lot, LotTransaction
from app.models.work_order import WorkOrder, WorkOrderComponent, WorkOrderComponentLot
from app.services.inventory import apply_inventory_delta
from app.services.locations import location_code
from app.services.lots import consume_lot_fefo, topup_lot
from app.services.work_orders import (
    blocking_job,
    reserved_by_higher_priority,
)

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


class WorkOrderFinishRequest(BaseModel):
    quantity_good: Decimal
    quantity_scrapped: Decimal = Decimal(0)
    scrap_reason: str | None = None
    # Required when the produced item is lot tracked and anything good came
    # off the line, because that stock has to land in a named batch.
    lot_number: str | None = None
    # not_started | consumed, required only on an under-run.
    remainder_disposition: str | None = None


class WorkOrderUpdateRequest(BaseModel):
    # Every field optional: a PATCH touches only what it sends. Omitted
    # fields keep the job's current value.
    quantity_planned: Decimal | None = None
    scheduled_start: date | None = None
    location_id: str | None = None
    notes: str | None = None
    assigned_operator_id: str | None = None


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
    # The finish form has to ask for a lot number for a batch-tracked product
    # and must not for anything else. Carried on the row the panel already has,
    # rather than making the UI fetch every product to find out.
    item_is_lot_tracked: bool
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
    assigned_operator_id: str | None
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
        item_is_lot_tracked=item.is_lot_tracked,
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
        assigned_operator_id=str(wo.assigned_operator_id) if wo.assigned_operator_id else None,
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


# max(2 x planned, planned + 10). A typo guard, not a business rule, which is
# why it is fixed rather than configurable. The additive term matters more than
# it looks: a plain doubling refuses a job planned for 1 that yielded 3,
# routine on a prototype line, and would feel broken to exactly the shops least
# able to work around it.
def _over_run_ceiling(planned: Decimal) -> Decimal:
    return max(planned * 2, planned + 10)


async def _return_components(
    db: AsyncSession, job: WorkOrder, current: CurrentUser, fraction: Decimal
) -> dict[str, str]:
    """Put `fraction` of what each component contributed back on the shelf.

    Writes, never commits: the caller owns the transaction. Shared by the
    under-run return and by cancelling a running job, so the two cannot drift
    apart into two different ways of moving the same stock.
    """
    rows = (
        await db.execute(
            select(WorkOrderComponent, Item)
            .join(Item, Item.id == WorkOrderComponent.item_id)
            .where(WorkOrderComponent.org_id == current.org_id, WorkOrderComponent.wo_id == job.id)
            .order_by(WorkOrderComponent.sequence)
        )
    ).all()

    returned: dict[str, str] = {}
    for component, item in rows:
        # Cap at what the job actually took: quantize can round a fraction of
        # 1 up past the consumed figure, and putting back more than came off
        # would invent stock.
        return_qty = min(
            (component.quantity_consumed * fraction).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ),
            component.quantity_consumed,
        )
        if return_qty <= 0:
            continue

        if item.is_lot_tracked:
            await _return_to_lots(db, component, return_qty, current)
        else:
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=job.location_id,
                delta=return_qty,
                transaction_type="production",
                created_by=current.id,
                reason="work order remainder",
            )
        component.quantity_consumed -= return_qty
        returned[item.sku] = str(return_qty)
    await db.flush()
    return returned


async def _return_to_lots(
    db: AsyncSession, component: WorkOrderComponent, quantity: Decimal, current: CurrentUser
) -> None:
    """Back into the lots this component was drawn from, largest draw first and
    never more than that draw. LotTransaction carries no job reference, so
    WorkOrderComponentLot is the only record of the split; guessing here (FEFO,
    or one arbitrary lot) would put stock in a batch it never came from and
    quietly corrupt expiry tracking.
    """
    draws = (
        await db.execute(
            select(WorkOrderComponentLot, Lot)
            .join(Lot, Lot.id == WorkOrderComponentLot.lot_id)
            .where(WorkOrderComponentLot.wo_component_id == component.id)
            .order_by(WorkOrderComponentLot.quantity.desc(), WorkOrderComponentLot.id)
        )
    ).all()

    remaining = quantity
    for draw, lot in draws:
        if remaining <= 0:
            break
        take = min(draw.quantity, remaining)
        await topup_lot(db, lot, take, None)
        db.add(
            LotTransaction(
                org_id=current.org_id,
                lot_id=lot.id,
                transaction_type="receipt",
                quantity=take,
                reason="work order remainder",
                created_by=current.id,
            )
        )
        # The row is what this job still holds, so shrink it. Without this a
        # second return against the same job would give the stock back twice.
        draw.quantity -= take
        remaining -= take
