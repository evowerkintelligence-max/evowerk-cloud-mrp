from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryBalance, InventoryTransaction
from app.models.location import Location
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id

router = APIRouter(prefix="/inventory", tags=["inventory"])


class ConsumeRequest(BaseModel):
    quantity: Decimal
    # Optional: defaults to the org's default location. See services.locations.
    location_id: str | None = None
    reason: str | None = None


class AdjustRequest(BaseModel):
    # Signed correction: +3 found more on the shelf, -3 found fewer. A cycle
    # count is entered as the delta from the system figure, not the new
    # absolute - that keeps the write a single guarded increment with no
    # read-modify-write race (see services.inventory.apply_inventory_delta).
    quantity: Decimal
    location_id: str | None = None
    reason: str | None = None


class TransferRequest(BaseModel):
    from_location_id: str
    to_location_id: str
    quantity: Decimal
    reason: str | None = None


class LocationQuantity(BaseModel):
    location_id: str
    location_code: str
    location_name: str
    quantity_on_hand: Decimal


class InventoryItemResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    unit_of_measure: str
    # Total across all locations; by_location has the per-location breakdown.
    quantity_on_hand: Decimal
    by_location: list[LocationQuantity]


class InventoryTransactionResponse(BaseModel):
    id: str
    location_id: str
    location_code: str
    transaction_type: str
    quantity_delta: Decimal
    reason: str | None
    created_by: str
    created_at: datetime


class InventoryDetailResponse(InventoryItemResponse):
    transactions: list[InventoryTransactionResponse]


def _reject_lot_tracked(item: Item) -> None:
    if item.is_lot_tracked:
        # Lot-tracked items own their on-hand through lots; letting them also
        # carry an inventory balance would create two disagreeing figures.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Item {item.sku} is lot-tracked - manage its on-hand through lots, not inventory",
        )


async def _by_location(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> list[LocationQuantity]:
    rows = await db.execute(
        select(InventoryBalance, Location)
        .join(Location, Location.id == InventoryBalance.location_id)
        .where(InventoryBalance.org_id == org_id, InventoryBalance.item_id == item_id)
        .order_by(Location.code)
    )
    return [
        LocationQuantity(
            location_id=str(loc.id),
            location_code=loc.code,
            location_name=loc.name,
            quantity_on_hand=bal.quantity_on_hand,
        )
        for bal, loc in rows.all()
    ]


def _item_response(item: Item, by_location: list[LocationQuantity]) -> InventoryItemResponse:
    return InventoryItemResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        unit_of_measure=item.unit_of_measure,
        # Decimal(0) (vs "0.0000") would serialize as "0" instead of matching
        # the Numeric(12,4) scale every DB-sourced balance carries - only
        # visible once an item can legitimately have zero location rows (e.g.
        # a fully-rejected PO receipt that never touches inventory).
        quantity_on_hand=sum((lq.quantity_on_hand for lq in by_location), Decimal("0.0000")),
        by_location=by_location,
    )


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
    current: CurrentUser = Depends(require_role("owner")),
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


@router.post("/{item_id}/adjust", response_model=InventoryItemResponse)
async def adjust_inventory(
    item_id: uuid.UUID,
    body: AdjustRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be a non-zero correction")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)

    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
        location_id=location_id,
        delta=body.quantity,
        transaction_type="adjustment",
        created_by=current.id,
        reason=body.reason,
    )
    await log_event(
        db,
        action="inventory.adjusted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={"delta": str(body.quantity), "location_id": str(location_id), "reason": body.reason},
    )
    await db.commit()
    return _item_response(item, await _by_location(db, current.org_id, item_id))


@router.post("/{item_id}/transfer", response_model=InventoryItemResponse)
async def transfer_inventory(
    item_id: uuid.UUID,
    body: TransferRequest,
    current: CurrentUser = Depends(require_role("owner")),
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
