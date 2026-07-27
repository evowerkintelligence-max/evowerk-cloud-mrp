from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.lot import LotTransaction
from app.models.sales import Customer, SalesOrder, SalesOrderLine
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id
from app.services.lots import consume_lot_fefo

router = APIRouter(prefix="/sales-orders", tags=["sales-orders"])

# Guards the so_number max+1 read-then-insert race, same as its buy-side twin
# MAX_PO_NUMBER_ATTEMPTS: two concurrent creates in one org can read the same
# max and collide on uq_sos_org_number.
MAX_SO_NUMBER_ATTEMPTS = 3

# Numeric(12, 4) on SalesOrderLine.quantity_ordered: 8 digits before the
# decimal point. Matches the planning grid write's own bound.
MAX_LINE_QUANTITY = 100_000_000


class SalesOrderLineInput(BaseModel):
    item_id: str
    quantity_ordered: Decimal
    sequence: int = 0


class SalesOrderLineResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    quantity_ordered: Decimal
    quantity_delivered: Decimal
    sequence: int


class SalesOrderCreateRequest(BaseModel):
    customer_id: str
    needed_by: date
    notes: str | None = None
    # Unlike a purchase order, an order is never created empty and filled
    # later: there is no draft state on the sell side, so the lines arrive
    # with the order.
    lines: list[SalesOrderLineInput]


class SalesOrderResponse(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    so_number: int
    status: str
    needed_by: date
    notes: str | None
    # Still open with the needed-by date behind us. The goods are still owed,
    # so the order keeps counting as demand (see
    # services.demand.load_committed_demand); this just lets the list say so.
    is_overdue: bool


class SalesOrderDetailResponse(SalesOrderResponse):
    lines: list[SalesOrderLineResponse]


def _so_to_response(so: SalesOrder, customer: Customer) -> SalesOrderResponse:
    return SalesOrderResponse(
        id=str(so.id),
        customer_id=str(so.customer_id),
        customer_name=customer.name,
        so_number=so.so_number,
        status=so.status,
        needed_by=so.needed_by,
        notes=so.notes,
        is_overdue=so.status == "open" and so.needed_by < datetime.now(timezone.utc).date(),
    )


async def _fetch_so_lines_with_items(
    db: AsyncSession, org_id: uuid.UUID, so_id: uuid.UUID, *, for_update: bool = False
) -> list[tuple[SalesOrderLine, Item]]:
    query = (
        select(SalesOrderLine, Item)
        .join(Item, Item.id == SalesOrderLine.item_id)
        # so_id is already org-verified by every caller; the org_id filter is
        # belt and braces, matching the rest of this module.
        .where(SalesOrderLine.org_id == org_id, SalesOrderLine.so_id == so_id)
        .order_by(SalesOrderLine.sequence)
    )
    if for_update:
        query = query.with_for_update(of=SalesOrderLine)
    result = await db.execute(query)
    return result.all()


def _line_to_response(line: SalesOrderLine, item: Item) -> SalesOrderLineResponse:
    return SalesOrderLineResponse(
        id=str(line.id),
        item_id=str(line.item_id),
        item_sku=item.sku,
        item_name=item.name,
        quantity_ordered=line.quantity_ordered,
        quantity_delivered=line.quantity_delivered,
        sequence=line.sequence,
    )


async def _fetch_lines_for_orders(
    db: AsyncSession, org_id: uuid.UUID, so_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SalesOrderLineResponse]]:
    """Every line for a page of orders in one query, grouped by order."""
    if not so_ids:
        return {}
    rows = (
        await db.execute(
            select(SalesOrderLine, Item)
            .join(Item, Item.id == SalesOrderLine.item_id)
            .where(SalesOrderLine.org_id == org_id, SalesOrderLine.so_id.in_(so_ids))
            .order_by(SalesOrderLine.sequence)
        )
    ).all()
    grouped: dict[uuid.UUID, list[SalesOrderLineResponse]] = defaultdict(list)
    for line, item in rows:
        grouped[line.so_id].append(_line_to_response(line, item))
    return grouped


async def _validate_lines(
    db: AsyncSession, org_id: uuid.UUID, lines_in: list[SalesOrderLineInput]
) -> None:
    if not lines_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "an order needs at least one line")
    for line_in in lines_in:
        if line_in.quantity_ordered <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
        # Numeric(12, 4) holds 8 integer digits. Postgres raises a numeric
        # overflow on anything larger, which would surface as a 500 rather
        # than a message the user can act on. Same bound the planning grid
        # write uses.
        if line_in.quantity_ordered >= MAX_LINE_QUANTITY:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "that quantity is too large")
        try:
            item_id = uuid.UUID(line_in.item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid item_id") from None
        exists = await db.scalar(
            select(Item.id).where(Item.org_id == org_id, Item.id == item_id)
        )
        if exists is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"item {line_in.item_id} not found")


@router.post("", response_model=SalesOrderDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_sales_order(
    body: SalesOrderCreateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    try:
        customer_id = uuid.UUID(body.customer_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid customer_id") from None
    customer = await db.scalar(
        select(Customer).where(Customer.org_id == current.org_id, Customer.id == customer_id)
    )
    if customer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer not found")
    if not customer.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"customer {customer.name} is inactive")

    await _validate_lines(db, current.org_id, body.lines)

    for attempt in range(MAX_SO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(SalesOrder.so_number)).where(SalesOrder.org_id == current.org_id)
        )
        so = SalesOrder(
            org_id=current.org_id,
            customer_id=customer_id,
            so_number=(max_number or 0) + 1,
            status="open",
            needed_by=body.needed_by,
            notes=body.notes,
            created_by=current.id,
        )
        # A per-attempt SAVEPOINT, not a full db.rollback(): a whole-transaction
        # rollback would expire the already-loaded `customer`, so the
        # `customer.name` read while building the response would trigger an
        # async refresh mid-response (MissingGreenlet).
        try:
            async with db.begin_nested():
                db.add(so)
                await db.flush()
            break
        except IntegrityError:
            if attempt == MAX_SO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "could not allocate an order number, please retry"
                ) from None

    for line_in in body.lines:
        db.add(
            SalesOrderLine(
                org_id=current.org_id,
                so_id=so.id,
                item_id=uuid.UUID(line_in.item_id),
                quantity_ordered=line_in.quantity_ordered,
                sequence=line_in.sequence,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="sales_order.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(so.id),
        metadata={"so_number": so.so_number, "customer_id": str(customer_id)},
    )
    await db.commit()
    return await _detail_response(db, so, customer)


async def _detail_response(
    db: AsyncSession, so: SalesOrder, customer: Customer
) -> SalesOrderDetailResponse:
    lines = [
        _line_to_response(line, item)
        for line, item in await _fetch_so_lines_with_items(db, so.org_id, so.id)
    ]
    base = _so_to_response(so, customer)
    return SalesOrderDetailResponse(**base.model_dump(), lines=lines)


@router.get("", response_model=list[SalesOrderDetailResponse])
async def list_sales_orders(
    q: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(SalesOrder, Customer)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .where(SalesOrder.org_id == current.org_id)
    )
    if q:
        query = query.where(
            or_(Customer.name.ilike(f"%{q}%"), cast(SalesOrder.so_number, String).ilike(f"%{q}%"))
        )
    if status_filter:
        query = query.where(SalesOrder.status == status_filter)
    query = query.order_by(SalesOrder.so_number).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()

    # The list has to show the product and quantity, so the lines ship with it.
    # One grouped query for the whole page rather than one per row: the panel
    # renders up to `limit` orders, and a per-row fetch made that many
    # round trips.
    lines_by_so = await _fetch_lines_for_orders(db, current.org_id, [so.id for so, _ in rows])
    return [
        SalesOrderDetailResponse(
            **_so_to_response(so, customer).model_dump(), lines=lines_by_so.get(so.id, [])
        )
        for so, customer in rows
    ]


@router.get("/{so_id}", response_model=SalesOrderDetailResponse)
async def get_sales_order(
    so_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    so = await get_or_404(db, SalesOrder, current.org_id, so_id, "Sales order not found")
    customer = await get_or_404(db, Customer, current.org_id, so.customer_id, "Customer not found")
    return await _detail_response(db, so, customer)


@router.patch("/{so_id}", response_model=SalesOrderDetailResponse)
async def update_sales_order_lines(
    so_id: uuid.UUID,
    lines_in: list[SalesOrderLineInput],
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    so = await get_or_404(
        db, SalesOrder, current.org_id, so_id, "Sales order not found", for_update=True
    )
    if so.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"order {so.so_number} is {so.status}")
    await _validate_lines(db, current.org_id, lines_in)

    await db.execute(SalesOrderLine.__table__.delete().where(SalesOrderLine.so_id == so.id))
    for line_in in lines_in:
        db.add(
            SalesOrderLine(
                org_id=current.org_id,
                so_id=so.id,
                item_id=uuid.UUID(line_in.item_id),
                quantity_ordered=line_in.quantity_ordered,
                sequence=line_in.sequence,
            )
        )
    # Only child rows changed, so SQLAlchemy's onupdate never fires on the
    # parent; touch it so updated_at still means "last changed".
    so.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        action="sales_order.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(so.id),
        metadata={"so_number": so.so_number, "line_count": len(lines_in)},
    )
    await db.commit()
    return await get_sales_order(so_id, current, db)


@router.post("/{so_id}/complete", response_model=SalesOrderDetailResponse)
async def complete_sales_order(
    so_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Ship the order: the goods leave stock and the order closes, in one
    transaction, so a shortfall on the second line rolls back the first line's
    movement too."""
    so = await get_or_404(
        db, SalesOrder, current.org_id, so_id, "Sales order not found", for_update=True
    )
    if so.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"order {so.so_number} is already {so.status}")

    # An order has no location of its own, so it ships from the org's default,
    # the same fallback /inventory/{item_id}/consume uses.
    location_id = await resolve_location_id(db, current.org_id, None)

    moved: dict[str, str] = {}
    for line, item in await _fetch_so_lines_with_items(db, current.org_id, so.id, for_update=True):
        outstanding = line.quantity_ordered - line.quantity_delivered
        if outstanding <= 0:
            continue
        # Two paths on purpose, do not collapse them into one. Lot-tracked
        # stock lives in its own subsystem; apply_inventory_delta does not
        # check for it, so sending a lot-tracked item down that path would
        # silently write an InventoryBalance row alongside the lots and
        # double-count the stock. This branch IS the guard. Non-lot stock has
        # no lots to draw from and must take the other path. Both raise 409
        # with a usable message when short, so there is no pre-flight
        # availability check here.
        if item.is_lot_tracked:
            consumed = await consume_lot_fefo(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=location_id,
                quantity=outstanding,
            )
            for entry in consumed:
                db.add(
                    LotTransaction(
                        org_id=current.org_id,
                        lot_id=entry.lot.id,
                        transaction_type="consumption",
                        quantity=entry.quantity,
                        reason="sales order",
                        created_by=current.id,
                    )
                )
        else:
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=location_id,
                delta=-outstanding,
                transaction_type="consumption",
                created_by=current.id,
                reason="sales order",
            )
        line.quantity_delivered = line.quantity_ordered
        moved[item.sku] = str(outstanding)

    so.status = "done"
    await db.flush()

    await log_event(
        db,
        action="sales_order.completed",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(so.id),
        metadata={"so_number": so.so_number, "moved": moved},
    )
    await db.commit()
    return await get_sales_order(so_id, current, db)


@router.post("/{so_id}/cancel", response_model=SalesOrderResponse)
async def cancel_sales_order(
    so_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    so = await get_or_404(
        db, SalesOrder, current.org_id, so_id, "Sales order not found", for_update=True
    )
    if so.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"order {so.so_number} is already {so.status}")

    # Cancelling moves no stock: nothing has shipped, it simply stops being
    # demand.
    so.status = "cancelled"
    await db.flush()

    await log_event(
        db,
        action="sales_order.cancelled",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(so.id),
        metadata={"so_number": so.so_number},
    )
    await db.commit()
    customer = await get_or_404(db, Customer, current.org_id, so.customer_id, "Customer not found")
    return _so_to_response(so, customer)
