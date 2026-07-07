from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.lot import Lot, LotTransaction

router = APIRouter(prefix="/lots", tags=["lots"])


class ConsumeRequest(BaseModel):
    quantity: Decimal
    reason: str | None = None


class LotResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
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


def _to_response(lot: Lot, item: Item) -> LotResponse:
    return LotResponse(
        id=str(lot.id),
        item_id=str(lot.item_id),
        item_sku=item.sku,
        item_name=item.name,
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


async def _get_item(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    result = await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


@router.get("", response_model=list[LotResponse])
async def list_lots(
    item_id: str | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Lot, Item).join(Item, Item.id == Lot.item_id).where(Lot.org_id == current.org_id)
    if item_id:
        try:
            query = query.where(Lot.item_id == uuid.UUID(item_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item_id") from None
    result = await db.execute(query.order_by(Item.sku, Lot.lot_number))
    return [_to_response(lot, item) for lot, item in result.all()]


@router.get("/{lot_id}", response_model=LotDetailResponse)
async def get_lot(
    lot_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lot = await _get_lot_or_404(db, current.org_id, lot_id)
    item = await _get_item(db, current.org_id, lot.item_id)

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
    base = _to_response(lot, item)
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

    # Guarded UPDATE, not read-then-check-then-write: two concurrent consumes
    # against the same lot could otherwise both read the same
    # quantity_remaining, both pass validation, and leave it negative. The
    # WHERE clause re-checks status/quantity atomically against whatever the
    # row actually holds at update time, not the Python-side value read above.
    new_remaining_expr = Lot.quantity_remaining - body.quantity
    result = await db.execute(
        update(Lot)
        .where(Lot.id == lot.id, Lot.quantity_remaining >= body.quantity, Lot.status != "on_hold")
        .values(
            quantity_remaining=new_remaining_expr,
            status=case((new_remaining_expr <= 0, "depleted"), else_="active"),
        )
        .returning(Lot.quantity_remaining, Lot.status)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Consuming {body.quantity} would exceed the quantity remaining, or the lot was put on "
            "hold concurrently",
        )
    lot.quantity_remaining, lot.status = row

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
    return _to_response(lot, item)


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
    return _to_response(lot, item)


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
    return _to_response(lot, item)
