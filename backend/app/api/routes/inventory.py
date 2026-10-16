from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryAdjustmentRequest, InventoryBalance, InventoryTransaction
from app.models.location import Location
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id
from app.api.routes.inventory_helpers import (
    AdjustRequest,
    CountRequest,
    ApproveRejectRequest,
    LocationQuantity,
    InventoryItemResponse,
    InventoryTransactionResponse,
    InventoryDetailResponse,
    AdjustmentRequestResponse,
    AdjustmentOutcome,
    _reject_lot_tracked,
    _by_location,
    _item_response,
    _current_on_hand,
    _adjustment_request_response,
    _post_or_queue_adjustment,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ConsumeRequest/TransferRequest stay here, not in inventory_helpers: another
# schema shares each name, so FastAPI names their OpenAPI components by module
# path and moving them would rename those components.
class ConsumeRequest(BaseModel):
    quantity: Decimal
    # Optional: defaults to the org's default location. See services.locations.
    location_id: str | None = None
    reason: str | None = None


class TransferRequest(BaseModel):
    from_location_id: str
    to_location_id: str
    quantity: Decimal
    reason: str | None = None



@router.get("", response_model=list[InventoryItemResponse])
async def list_inventory(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One row per item that has any on-hand, with its total and per-location
    breakdown."""
    rows = await db.execute(
        select(InventoryBalance, Item, Location)
        .join(Item, Item.id == InventoryBalance.item_id)
        .join(Location, Location.id == InventoryBalance.location_id)
        .where(InventoryBalance.org_id == current.org_id)
        .order_by(Item.sku, Location.code)
    )
    items: dict[uuid.UUID, Item] = {}
    by_item: dict[uuid.UUID, list[LocationQuantity]] = {}
    for bal, item, loc in rows.all():
        items[item.id] = item
        by_item.setdefault(item.id, []).append(
            LocationQuantity(
                location_id=str(loc.id),
                location_code=loc.code,
                location_name=loc.name,
                quantity_on_hand=bal.quantity_on_hand,
            )
        )
    return [_item_response(items[item_id], locs) for item_id, locs in by_item.items()]


@router.get("/adjustment-requests", response_model=list[AdjustmentRequestResponse])
async def list_adjustment_requests(
    status_filter: str | None = Query(default="pending", alias="status"),
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    # Registered ahead of GET /{item_id}: Starlette matches "/{item_id}" as a
    # literal single-segment pattern (uuid.UUID conversion happens after the
    # path already matched), so "/adjustment-requests" would otherwise be
    # swallowed by /{item_id} and 422 trying to parse it as a UUID.
    query = (
        select(InventoryAdjustmentRequest, Item, Location)
        .join(Item, Item.id == InventoryAdjustmentRequest.item_id)
        .join(Location, Location.id == InventoryAdjustmentRequest.location_id)
        .where(InventoryAdjustmentRequest.org_id == current.org_id)
        .order_by(InventoryAdjustmentRequest.requested_at.desc())
    )
    if status_filter:
        query = query.where(InventoryAdjustmentRequest.status == status_filter)
    rows = await db.execute(query)
    return [_adjustment_request_response(req, item, loc) for req, item, loc in rows.all()]


@router.get("/{item_id}", response_model=InventoryDetailResponse)
async def get_inventory(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    by_location = await _by_location(db, current.org_id, item_id)

    tx_result = await db.execute(
        select(InventoryTransaction, Location)
        .join(Location, Location.id == InventoryTransaction.location_id)
        .where(InventoryTransaction.org_id == current.org_id, InventoryTransaction.item_id == item_id)
        .order_by(InventoryTransaction.created_at)
    )
    transactions = [
        InventoryTransactionResponse(
            id=str(tx.id),
            location_id=str(tx.location_id),
            location_code=loc.code,
            transaction_type=tx.transaction_type,
            quantity_delta=tx.quantity_delta,
            reason=tx.reason,
            counted_quantity=tx.counted_quantity,
            expected_quantity=tx.expected_quantity,
            reason_note=tx.reason_note,
            created_by=str(tx.created_by),
            created_at=tx.created_at,
        )
        for tx, loc in tx_result.all()
    ]
    base = _item_response(item, by_location)
    return InventoryDetailResponse(**base.model_dump(), transactions=transactions)


@router.post("/{item_id}/consume", response_model=InventoryItemResponse)
async def consume_inventory(
    item_id: uuid.UUID,
    body: ConsumeRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)

    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
        location_id=location_id,
        delta=-body.quantity,
        transaction_type="consumption",
        created_by=current.id,
        reason=body.reason,
    )
    await log_event(
        db,
        action="inventory.consumed",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={"quantity": str(body.quantity), "location_id": str(location_id), "reason": body.reason},
    )
    await db.commit()
    return _item_response(item, await _by_location(db, current.org_id, item_id))


@router.post("/{item_id}/adjust", response_model=AdjustmentOutcome)
async def adjust_inventory(
    item_id: uuid.UUID,
    body: AdjustRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be a non-zero correction")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    current_on_hand = await _current_on_hand(db, current.org_id, item_id, location_id)

    outcome = await _post_or_queue_adjustment(
        db,
        org_id=current.org_id,
        role=current.role,
        item=item,
        location_id=location_id,
        delta=body.quantity,
        current_on_hand=current_on_hand,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
        counted_quantity=None,
        created_by=current.id,
    )
    await log_event(
        db,
        action="inventory.adjusted" if outcome.applied else "inventory.adjustment_requested",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "delta": str(body.quantity),
            "location_id": str(location_id),
            "reason_code": body.reason_code,
        },
    )
    await db.commit()
    return outcome


@router.post("/{item_id}/count", response_model=AdjustmentOutcome)
async def count_inventory(
    item_id: uuid.UUID,
    body: CountRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    current_on_hand = await _current_on_hand(db, current.org_id, item_id, location_id)
    delta = body.counted_quantity - current_on_hand
    if delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no variance - nothing to post")

    outcome = await _post_or_queue_adjustment(
        db,
        org_id=current.org_id,
        role=current.role,
        item=item,
        location_id=location_id,
        delta=delta,
        current_on_hand=current_on_hand,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
        counted_quantity=body.counted_quantity,
        created_by=current.id,
    )
    await log_event(
        db,
        action="inventory.adjusted" if outcome.applied else "inventory.adjustment_requested",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "delta": str(delta),
            "counted_quantity": str(body.counted_quantity),
            "location_id": str(location_id),
            "reason_code": body.reason_code,
        },
    )
    await db.commit()
    return outcome


@router.post("/{item_id}/transfer", response_model=InventoryItemResponse)
async def transfer_inventory(
    item_id: uuid.UUID,
    body: TransferRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    """Move stock between two locations in one atomic operation: a guarded
    consume at the source (fails with 409 if it hasn't got the quantity) plus a
    receipt at the destination. Total on-hand for the item is unchanged."""
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
    if body.from_location_id == body.to_location_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "from and to locations must differ")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    from_id = await resolve_location_id(db, current.org_id, body.from_location_id)
    to_id = await resolve_location_id(db, current.org_id, body.to_location_id)

    # Source first: if it lacks the quantity this raises 409 before anything is
    # written to the destination.
    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
        location_id=from_id,
        delta=-body.quantity,
        transaction_type="transfer_out",
        created_by=current.id,
        reason=body.reason,
    )
    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
        location_id=to_id,
        delta=body.quantity,
        transaction_type="transfer_in",
        created_by=current.id,
        reason=body.reason,
    )
    await log_event(
        db,
        action="inventory.transferred",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "quantity": str(body.quantity),
            "from_location_id": str(from_id),
            "to_location_id": str(to_id),
        },
    )
    await db.commit()
    return _item_response(item, await _by_location(db, current.org_id, item_id))


@router.post(
    "/adjustment-requests/{request_id}/approve", response_model=AdjustmentRequestResponse
)
async def approve_adjustment_request(
    request_id: uuid.UUID,
    body: ApproveRejectRequest = ApproveRejectRequest(),
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    # Locked for the rest of this transaction: without it, two concurrent
    # approvals (or an approve racing a reject) can both read status="pending"
    # before either commits and both post the delta - same class of race
    # purchase_orders.py guards against on PO submit/receive.
    req = await get_or_404(
        db, InventoryAdjustmentRequest, current.org_id, request_id, "Adjustment request not found",
        for_update=True,
    )
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request already {req.status}")

    item = await get_or_404(db, Item, current.org_id, req.item_id, "Item not found")
    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=req.item_id,
        location_id=req.location_id,
        delta=req.quantity_delta,
        transaction_type="adjustment",
        created_by=req.requested_by,
        reason=req.reason_code,
        reason_note=req.reason_note,
        counted_quantity=req.counted_quantity,
        expected_quantity=req.expected_quantity_at_submit if req.counted_quantity is not None else None,
    )
    req.status = "approved"
    req.reviewed_by = current.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = body.review_note
    await log_event(
        db,
        action="inventory.adjustment_approved",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(req.id),
        metadata={"item_id": str(req.item_id), "delta": str(req.quantity_delta)},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, req.location_id, "Location not found")
    return _adjustment_request_response(req, item, location)


@router.post(
    "/adjustment-requests/{request_id}/reject", response_model=AdjustmentRequestResponse
)
async def reject_adjustment_request(
    request_id: uuid.UUID,
    body: ApproveRejectRequest = ApproveRejectRequest(),
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    # Locked for the rest of this transaction: see approve_adjustment_request's
    # matching comment - same race between two concurrent reviewers.
    req = await get_or_404(
        db, InventoryAdjustmentRequest, current.org_id, request_id, "Adjustment request not found",
        for_update=True,
    )
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request already {req.status}")

    item = await get_or_404(db, Item, current.org_id, req.item_id, "Item not found")
    req.status = "rejected"
    req.reviewed_by = current.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = body.review_note
    await log_event(
        db,
        action="inventory.adjustment_rejected",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(req.id),
        metadata={"item_id": str(req.item_id), "review_note": body.review_note},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, req.location_id, "Location not found")
    return _adjustment_request_response(req, item, location)
