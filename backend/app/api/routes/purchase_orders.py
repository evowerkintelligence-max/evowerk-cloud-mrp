from __future__ import annotations

import uuid
from datetime import date
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
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, PurchaseOrderReceiptLine, Supplier
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id
from app.services.lots import find_or_create_lot

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

# Guards the po_number max+1 read-then-insert race: two concurrent creates in
# the same org can both read the same max and collide on uq_pos_org_number.
# Retry a few times rather than surfacing an unhandled 500.
MAX_PO_NUMBER_ATTEMPTS = 3


class PurchaseOrderLineInput(BaseModel):
    item_id: str
    quantity_ordered: Decimal
    unit_price: Decimal
    sequence: int = 0


class PurchaseOrderLineResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    item_is_lot_tracked: bool
    quantity_ordered: Decimal
    quantity_received: Decimal
    unit_price: Decimal
    sequence: int


class ReceiveLineInput(BaseModel):
    po_line_id: str
    quantity: Decimal
    # Portion of `quantity` that failed inspection - not additional to it.
    # See PurchaseOrderReceiptLine.quantity_rejected for the full contract.
    quantity_rejected: Decimal = Decimal(0)
    reject_reason: str | None = None
    # Required if the line's item is lot-tracked, rejected otherwise - see
    # receive_purchase_order's validation loop.
    lot_number: str | None = None
    expiry_date: date | None = None
    # Which location the stock lands in. Defaults to the org's default location.
    location_id: str | None = None


class PurchaseOrderCreateRequest(BaseModel):
    supplier_id: str
    expected_delivery_date: date | None = None


class PurchaseOrderResponse(BaseModel):
    id: str
    supplier_id: str
    supplier_name: str
    po_number: int
    status: str
    expected_delivery_date: date | None


class PurchaseOrderDetailResponse(PurchaseOrderResponse):
    lines: list[PurchaseOrderLineResponse]


async def _po_to_response(
    db: AsyncSession, po: PurchaseOrder, supplier: Supplier | None = None
) -> PurchaseOrderResponse:
    if supplier is None:
        supplier = await get_or_404(db, Supplier, po.org_id, po.supplier_id, "Supplier not found")
    return PurchaseOrderResponse(
        id=str(po.id),
        supplier_id=str(po.supplier_id),
        supplier_name=supplier.name,
        po_number=po.po_number,
        status=po.status,
        expected_delivery_date=po.expected_delivery_date,
    )


@router.post("", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    body: PurchaseOrderCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        supplier_id = uuid.UUID(body.supplier_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid supplier_id") from None
    supplier = await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")

    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(PurchaseOrder.po_number)).where(PurchaseOrder.org_id == current.org_id)
        )
        po = PurchaseOrder(
            org_id=current.org_id,
            supplier_id=supplier_id,
            po_number=(max_number or 0) + 1,
            status="draft",
            expected_delivery_date=body.expected_delivery_date,
            created_by=current.id,
        )
        # A per-attempt SAVEPOINT, not a full db.rollback(): two concurrent
        # creates can collide on uq_pos_org_number, but a whole-transaction
        # rollback here would also expire the already-loaded `supplier`, so the
        # `supplier.name` read in _po_to_response below would trigger an async
        # refresh mid-response (MissingGreenlet). The savepoint rolls back only
        # the failed insert and leaves the outer transaction and supplier intact.
        try:
            async with db.begin_nested():
                db.add(po)
                await db.flush()
            break
        except IntegrityError:
            if attempt == MAX_PO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a PO number, please retry"
                ) from None

    await log_event(
        db,
        action="po.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"supplier_id": str(supplier_id), "po_number": po.po_number},
    )
    await db.commit()
    return await _po_to_response(db, po, supplier)


@router.get("", response_model=list[PurchaseOrderResponse])
async def list_purchase_orders(
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(PurchaseOrder, Supplier)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(PurchaseOrder.org_id == current.org_id)
    )
    if q:
        query = query.where(
            or_(Supplier.name.ilike(f"%{q}%"), cast(PurchaseOrder.po_number, String).ilike(f"%{q}%"))
        )
    query = query.order_by(PurchaseOrder.po_number).limit(limit).offset(offset)
    result = await db.execute(query)
    return [await _po_to_response(db, po, supplier) for po, supplier in result.all()]


@router.get("/{po_id}", response_model=PurchaseOrderDetailResponse)
async def get_purchase_order(
    po_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    po = await get_or_404(db, PurchaseOrder, current.org_id, po_id, "Purchase order not found")
    supplier = await get_or_404(db, Supplier, current.org_id, po.supplier_id, "Supplier not found")

    lines_result = await db.execute(
        select(PurchaseOrderLine, Item)
        .join(Item, Item.id == PurchaseOrderLine.item_id)
        .where(PurchaseOrderLine.po_id == po.id)
        .order_by(PurchaseOrderLine.sequence)
    )
    lines = [
        PurchaseOrderLineResponse(
            id=str(line.id),
            item_id=str(line.item_id),
            item_sku=item.sku,
            item_name=item.name,
            item_is_lot_tracked=item.is_lot_tracked,
            quantity_ordered=line.quantity_ordered,
            quantity_received=line.quantity_received,
            unit_price=line.unit_price,
            sequence=line.sequence,
        )
        for line, item in lines_result.all()
    ]

    base = await _po_to_response(db, po, supplier)
    return PurchaseOrderDetailResponse(**base.model_dump(), lines=lines)


@router.patch("/{po_id}", response_model=PurchaseOrderDetailResponse)
async def update_purchase_order_lines(
    po_id: uuid.UUID,
    lines_in: list[PurchaseOrderLineInput],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    po = await get_or_404(
        db, PurchaseOrder, current.org_id, po_id, "Purchase order not found", for_update=True
    )
    if po.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft purchase orders can be edited")
    if not lines_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A purchase order needs at least one line")

    for line_in in lines_in:
        if line_in.quantity_ordered <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity_ordered must be positive")
        if line_in.unit_price < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unit_price can't be negative")
        try:
            item_id = uuid.UUID(line_in.item_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item_id") from None
        await get_or_404(db, Item, current.org_id, item_id, "Item not found")

    await db.execute(PurchaseOrderLine.__table__.delete().where(PurchaseOrderLine.po_id == po.id))
    for line_in in lines_in:
        db.add(
            PurchaseOrderLine(
                org_id=current.org_id,
                po_id=po.id,
                item_id=uuid.UUID(line_in.item_id),
                quantity_ordered=line_in.quantity_ordered,
                unit_price=line_in.unit_price,
                sequence=line_in.sequence,
            )
        )
    await db.flush()

    await log_event(
        db,
        action="po.lines_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"line_count": len(lines_in)},
    )
    await db.commit()
    return await get_purchase_order(po_id, current, db)


@router.post("/{po_id}/submit", response_model=PurchaseOrderResponse)
async def submit_purchase_order(
    po_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    po = await get_or_404(
        db, PurchaseOrder, current.org_id, po_id, "Purchase order not found", for_update=True
    )
    if po.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft purchase orders can be submitted")

    has_lines = await db.scalar(
        select(func.count()).select_from(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id)
    )
    if not has_lines:
        raise HTTPException(status.HTTP_409_CONFLICT, "Can't submit a purchase order with no lines")

    po.status = "submitted"
    await db.flush()

    await log_event(
        db,
        action="po.submitted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"po_number": po.po_number},
    )
    await db.commit()
    return await _po_to_response(db, po)


@router.post("/{po_id}/receive", response_model=PurchaseOrderDetailResponse)
async def receive_purchase_order(
    po_id: uuid.UUID,
    receipts_in: list[ReceiveLineInput],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    po = await get_or_404(
        db, PurchaseOrder, current.org_id, po_id, "Purchase order not found", for_update=True
    )
    if po.status not in ("submitted", "partially_received"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only submitted or partially received purchase orders can receive"
        )
    if not receipts_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide at least one line to receive")

    all_lines_result = await db.execute(
        select(PurchaseOrderLine, Item)
        .join(Item, Item.id == PurchaseOrderLine.item_id)
        .where(PurchaseOrderLine.po_id == po.id)
        .with_for_update(of=PurchaseOrderLine)
    )
    lines_by_id: dict[uuid.UUID, PurchaseOrderLine] = {}
    items_by_line_id: dict[uuid.UUID, Item] = {}
    for line, item in all_lines_result.all():
        lines_by_id[line.id] = line
        items_by_line_id[line.id] = item

    # Validate every receipt before writing any of them, so a bad line in the
    # request can't leave a partial write applied.
    for receipt in receipts_in:
        if receipt.quantity <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
        try:
            line_id = uuid.UUID(receipt.po_line_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid po_line_id") from None
        line = lines_by_id.get(line_id)
        if line is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order line not found")
        if line.quantity_received + receipt.quantity > line.quantity_ordered:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Receiving {receipt.quantity} on line {line_id} would exceed the ordered quantity",
            )
        if receipt.quantity_rejected < 0 or receipt.quantity_rejected > receipt.quantity:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "quantity_rejected must be between 0 and quantity"
            )
        item = items_by_line_id[line_id]
        if item.is_lot_tracked and not receipt.lot_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"lot_number is required for lot-tracked item {item.sku}"
            )
        if not item.is_lot_tracked and receipt.lot_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Item {item.sku} is not lot-tracked - remove lot_number"
            )

    for receipt in receipts_in:
        line_id = uuid.UUID(receipt.po_line_id)
        line = lines_by_id[line_id]
        item = items_by_line_id[line_id]
        line.quantity_received += receipt.quantity
        accepted_quantity = receipt.quantity - receipt.quantity_rejected

        receipt_line = PurchaseOrderReceiptLine(
            id=uuid.uuid4(),
            org_id=current.org_id,
            po_line_id=line.id,
            quantity=receipt.quantity,
            quantity_rejected=receipt.quantity_rejected,
            reject_reason=receipt.reject_reason,
            received_by=current.id,
        )
        db.add(receipt_line)

        if accepted_quantity == 0:
            # Fully rejected: nothing to apply to inventory/lot stock.
            continue

        # Defaults to the org's default location if unspecified - see
        # services.locations. Shared by both branches below.
        location_id = await resolve_location_id(db, current.org_id, receipt.location_id)

        if item.is_lot_tracked:
            lot_number = receipt.lot_number
            if lot_number is None:
                # Unreachable given the validation loop above already checked
                # this, but a real guard rather than an assert (which
                # vanishes under python -O) that also satisfies the type
                # checker that lot_number is str, not str | None, below.
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "lot_number is required")
            lot = await find_or_create_lot(
                db,
                org_id=current.org_id,
                item_id=item.id,
                lot_number=lot_number,
                location_id=location_id,
                quantity=accepted_quantity,
                expiry_date=receipt.expiry_date,
                created_by=current.id,
            )
            db.add(
                LotTransaction(
                    org_id=current.org_id,
                    lot_id=lot.id,
                    transaction_type="receipt",
                    quantity=accepted_quantity,
                    source_receipt_line_id=receipt_line.id,
                    created_by=current.id,
                )
            )
        else:
            # Non-lot-tracked: the received quantity lands in the general
            # inventory on-hand ledger at the target location, so this item's
            # stock is real for MRP netting instead of untracked - see
            # services.inventory.
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=location_id,
                delta=accepted_quantity,
                transaction_type="receipt",
                created_by=current.id,
                source_receipt_line_id=receipt_line.id,
            )

    # Recompute status by scanning ALL lines, not just the ones just received -
    # a partial receipt on one line must not flip status to "received" while
    # another untouched line is still at 0.
    if all(line.quantity_received >= line.quantity_ordered for line in lines_by_id.values()):
        po.status = "received"
    else:
        po.status = "partially_received"
    await db.flush()

    await log_event(
        db,
        action="po.received",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"lines_received": len(receipts_in), "new_status": po.status},
    )
    await db.commit()
    return await get_purchase_order(po_id, current, db)


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse)
async def cancel_purchase_order(
    po_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    po = await get_or_404(
        db, PurchaseOrder, current.org_id, po_id, "Purchase order not found", for_update=True
    )
    # Status alone is authoritative here: it already flips to
    # partially_received the instant any receiving happens, so gating on
    # draft/submitted is sufficient - no separate per-line check needed.
    if po.status not in ("draft", "submitted"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only draft or submitted purchase orders can be cancelled"
        )

    po.status = "cancelled"
    await db.flush()

    await log_event(
        db,
        action="po.cancelled",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(po.id),
        metadata={"po_number": po.po_number},
    )
    await db.commit()
    return await _po_to_response(db, po)
