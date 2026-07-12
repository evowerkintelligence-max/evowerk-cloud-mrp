from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.location import Location
from app.models.lot import Lot, LotTransaction
from app.services.locations import resolve_location_id
from app.services.lots import consume_lot_fefo, consume_lot_guarded, find_or_create_lot

router = APIRouter(prefix="/lots", tags=["lots"])


class ConsumeRequest(BaseModel):
    quantity: Decimal
    reason: str | None = None


class TransferRequest(BaseModel):
    to_location_id: str
    quantity: Decimal
    reason: str | None = None


class FefoConsumeRequest(BaseModel):
    item_id: str
    location_id: str
    quantity: Decimal
    reason: str | None = None


class LotConsumptionResponse(BaseModel):
    lot_id: str
    lot_number: str
    quantity_consumed: Decimal
    quantity_remaining: Decimal
    expiry_date: date | None


class FefoConsumeResponse(BaseModel):
    lines: list[LotConsumptionResponse]


class LotResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    location_id: str
    location_code: str
    lot_number: str
    quantity_received: Decimal
    quantity_remaining: Decimal
    status: str
    expiry_date: date | None


class LotTransactionResponse(BaseModel):
    id: str
    transaction_type: str
    quantity: Decimal
    reason: str | None
    created_by: str
    created_at: datetime


class LotDetailResponse(LotResponse):
    transactions: list[LotTransactionResponse]


def _to_response(lot: Lot, item: Item, location: Location) -> LotResponse:
    return LotResponse(
        id=str(lot.id),
        item_id=str(lot.item_id),
        item_sku=item.sku,
        item_name=item.name,
        location_id=str(lot.location_id),
        location_code=location.code,
        lot_number=lot.lot_number,
        quantity_received=lot.quantity_received,
        quantity_remaining=lot.quantity_remaining,
        status=lot.status,
        expiry_date=lot.expiry_date,
    )


async def _get_lot_or_404(db: AsyncSession, org_id: uuid.UUID, lot_id: uuid.UUID) -> Lot:
    result = await db.execute(select(Lot).where(Lot.id == lot_id, Lot.org_id == org_id))
    lot = result.scalar_one_or_none()
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found")
    return lot


async def _get_location(db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID) -> Location:
    result = await db.execute(select(Location).where(Location.id == location_id, Location.org_id == org_id))
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    return location


async def _get_item(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    result = await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


LOT_STATUSES = ("active", "on_hold", "depleted")


@router.get("", response_model=list[LotResponse])
async def list_lots(
    item_id: str | None = None,
    location_id: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Lot, Item, Location)
        .join(Item, Item.id == Lot.item_id)
        .join(Location, Location.id == Lot.location_id)
        .where(Lot.org_id == current.org_id)
    )
    if item_id:
        try:
            query = query.where(Lot.item_id == uuid.UUID(item_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item_id") from None
    if location_id:
        try:
            query = query.where(Lot.location_id == uuid.UUID(location_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid location_id") from None
    if status_filter:
        if status_filter not in LOT_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {sorted(LOT_STATUSES)}")
        query = query.where(Lot.status == status_filter)

    # Earliest-expiring first (nulls last) so the lot to use next under FEFO
    # is always at the top - ties broken the old way for stability.
    query = query.order_by(Lot.expiry_date.is_(None), Lot.expiry_date, Item.sku, Lot.lot_number, Location.code)
    result = await db.execute(query.limit(limit).offset(offset))
    return [_to_response(lot, item, location) for lot, item, location in result.all()]


@router.get("/{lot_id}", response_model=LotDetailResponse)
async def get_lot(
    lot_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    item = await _get_item(db, current.org_id, lot.item_id)
    location = await _get_location(db, current.org_id, lot.location_id)

    tx_result = await db.execute(
        select(LotTransaction).where(LotTransaction.lot_id == lot.id).order_by(LotTransaction.created_at)
    )
    transactions = [
        LotTransactionResponse(
            id=str(tx.id),
            transaction_type=tx.transaction_type,
            quantity=tx.quantity,
            reason=tx.reason,
            created_by=str(tx.created_by),
            created_at=tx.created_at,
        )
        for tx in tx_result.scalars().all()
    ]
    base = _to_response(lot, item, location)
    return LotDetailResponse(**base.model_dump(), transactions=transactions)


@router.post("/{lot_id}/consume", response_model=LotResponse)
async def consume_lot(
    lot_id: uuid.UUID,
    body: ConsumeRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")

    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    if lot.status == "on_hold":
        raise HTTPException(status.HTTP_409_CONFLICT, "Lot is on hold and can't be consumed")

    await consume_lot_guarded(db, lot, body.quantity)

    db.add(
        LotTransaction(
            org_id=current.org_id,
            lot_id=lot.id,
            transaction_type="consumption",
            quantity=body.quantity,
            reason=body.reason,
            created_by=current.id,
        )
    )
    await log_event(
        db,
        action="lot.consumed",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(lot.id),
        metadata={"quantity": str(body.quantity), "reason": body.reason},
    )
    await db.commit()
    item = await _get_item(db, current.org_id, lot.item_id)
    location = await _get_location(db, current.org_id, lot.location_id)
    return _to_response(lot, item, location)


@router.post("/consume-fefo", response_model=FefoConsumeResponse)
async def consume_lots_fefo(
    body: FefoConsumeRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Consume a quantity of an item at a location without picking a lot -
    earliest-expiring active stock is used first, splitting across lots if
    one isn't enough. See services.lots.consume_lot_fefo."""
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
    try:
        item_id = uuid.UUID(body.item_id)
        location_id = uuid.UUID(body.location_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item_id or location_id") from None

    await _get_item(db, current.org_id, item_id)
    await _get_location(db, current.org_id, location_id)

    consumed = await consume_lot_fefo(
        db, org_id=current.org_id, item_id=item_id, location_id=location_id, quantity=body.quantity
    )
    for entry in consumed:
        db.add(
            LotTransaction(
                org_id=current.org_id,
                lot_id=entry.lot.id,
                transaction_type="consumption",
                quantity=entry.quantity,
                reason=body.reason,
                created_by=current.id,
            )
        )
    await log_event(
        db,
        action="lot.consumed_fefo",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "quantity": str(body.quantity),
            "location_id": str(location_id),
            "lots": [str(entry.lot.id) for entry in consumed],
        },
    )
    await db.commit()
    return FefoConsumeResponse(
        lines=[
            LotConsumptionResponse(
                lot_id=str(entry.lot.id),
                lot_number=entry.lot.lot_number,
                quantity_consumed=entry.quantity,
                quantity_remaining=entry.lot.quantity_remaining,
                expiry_date=entry.lot.expiry_date,
            )
            for entry in consumed
        ]
    )


@router.post("/{lot_id}/transfer", response_model=LotResponse)
async def transfer_lot(
    lot_id: uuid.UUID,
    body: TransferRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Move quantity from this lot to the same lot_number at another location -
    the destination row is found or created (mirrors PO receiving), and the
    source is debited with a guarded consume, so a source that lacks the
    quantity 409s before anything is written to the destination. Returns the
    *source* lot's new state (its quantity_remaining reflects this location
    only, same convention as inventory transfer's per-location on-hand)."""
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")

    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    if lot.status == "on_hold":
        raise HTTPException(status.HTTP_409_CONFLICT, "Lot is on hold and can't be transferred")
    to_location_id = await resolve_location_id(db, current.org_id, body.to_location_id)
    if to_location_id == lot.location_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "from and to locations must differ")

    await consume_lot_guarded(db, lot, body.quantity)
    dest_lot = await find_or_create_lot(
        db,
        org_id=current.org_id,
        item_id=lot.item_id,
        lot_number=lot.lot_number,
        location_id=to_location_id,
        quantity=body.quantity,
        expiry_date=lot.expiry_date,
        created_by=current.id,
    )

    db.add(
        LotTransaction(
            org_id=current.org_id,
            lot_id=lot.id,
            transaction_type="transfer_out",
            quantity=body.quantity,
            reason=body.reason,
            created_by=current.id,
        )
    )
    db.add(
        LotTransaction(
            org_id=current.org_id,
            lot_id=dest_lot.id,
            transaction_type="transfer_in",
            quantity=body.quantity,
            reason=body.reason,
            created_by=current.id,
        )
    )
    await log_event(
        db,
        action="lot.transferred",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(lot.id),
        metadata={
            "quantity": str(body.quantity),
            "from_location_id": str(lot.location_id),
            "to_location_id": str(to_location_id),
        },
    )
    await db.commit()
    item = await _get_item(db, current.org_id, lot.item_id)
    location = await _get_location(db, current.org_id, lot.location_id)
    return _to_response(lot, item, location)


@router.post("/{lot_id}/hold", response_model=LotResponse)
async def hold_lot(
    lot_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    lot.status = "on_hold"
    await db.flush()

    await log_event(
        db, action="lot.held", org_id=current.org_id, actor_user_id=current.id, target=str(lot.id)
    )
    await db.commit()
    item = await _get_item(db, current.org_id, lot.item_id)
    location = await _get_location(db, current.org_id, lot.location_id)
    return _to_response(lot, item, location)


@router.post("/{lot_id}/release", response_model=LotResponse)
async def release_lot(
    lot_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    if lot.status != "on_hold":
        raise HTTPException(status.HTTP_409_CONFLICT, "Lot is not on hold")
    lot.status = "active" if lot.quantity_remaining > 0 else "depleted"
    await db.flush()

    await log_event(
        db, action="lot.released", org_id=current.org_id, actor_user_id=current.id, target=str(lot.id)
    )
    await db.commit()
    item = await _get_item(db, current.org_id, lot.item_id)
    location = await _get_location(db, current.org_id, lot.location_id)
    return _to_response(lot, item, location)
