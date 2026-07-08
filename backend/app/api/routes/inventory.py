from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryBalance, InventoryTransaction
from app.services.inventory import apply_inventory_delta

router = APIRouter(prefix="/inventory", tags=["inventory"])


class ConsumeRequest(BaseModel):
    quantity: Decimal
    reason: str | None = None


class AdjustRequest(BaseModel):
    # Signed correction: +3 found more on the shelf, -3 found fewer. A cycle
    # count is entered as the delta from the system figure, not the new
    # absolute - that keeps the write a single guarded increment with no
    # read-modify-write race (see services.inventory.apply_inventory_delta).
    quantity: Decimal
    reason: str | None = None


class InventoryBalanceResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    unit_of_measure: str
    quantity_on_hand: Decimal


class InventoryTransactionResponse(BaseModel):
    id: str
    transaction_type: str
    quantity_delta: Decimal
    reason: str | None
    created_by: str
    created_at: datetime


class InventoryDetailResponse(InventoryBalanceResponse):
    transactions: list[InventoryTransactionResponse]


def _balance_response(item: Item, quantity_on_hand: Decimal) -> InventoryBalanceResponse:
    return InventoryBalanceResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        unit_of_measure=item.unit_of_measure,
        quantity_on_hand=quantity_on_hand,
    )


async def _get_item_or_404(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    item = (
        await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


def _reject_lot_tracked(item: Item) -> None:
    if item.is_lot_tracked:
        # Lot-tracked items own their on-hand through lots; letting them also
        # carry an inventory balance would create two disagreeing figures.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Item {item.sku} is lot-tracked - manage its on-hand through lots, not inventory",
        )


async def _on_hand(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Decimal:
    qty = (
        await db.execute(
            select(InventoryBalance.quantity_on_hand).where(
                InventoryBalance.org_id == org_id, InventoryBalance.item_id == item_id
            )
        )
    ).scalar_one_or_none()
    return qty if qty is not None else Decimal(0)


@router.get("", response_model=list[InventoryBalanceResponse])
async def list_inventory(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InventoryBalance, Item)
        .join(Item, Item.id == InventoryBalance.item_id)
        .where(InventoryBalance.org_id == current.org_id)
        .order_by(Item.sku)
    )
    return [_balance_response(item, bal.quantity_on_hand) for bal, item in result.all()]


@router.get("/{item_id}", response_model=InventoryDetailResponse)
async def get_inventory(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_item_or_404(db, current.org_id, item_id)
    _reject_lot_tracked(item)
    on_hand = await _on_hand(db, current.org_id, item_id)

    tx_result = await db.execute(
        select(InventoryTransaction)
        .where(InventoryTransaction.org_id == current.org_id, InventoryTransaction.item_id == item_id)
        .order_by(InventoryTransaction.created_at)
    )
    transactions = [
        InventoryTransactionResponse(
            id=str(tx.id),
            transaction_type=tx.transaction_type,
            quantity_delta=tx.quantity_delta,
            reason=tx.reason,
            created_by=str(tx.created_by),
            created_at=tx.created_at,
        )
        for tx in tx_result.scalars().all()
    ]
    base = _balance_response(item, on_hand)
    return InventoryDetailResponse(**base.model_dump(), transactions=transactions)


@router.post("/{item_id}/consume", response_model=InventoryBalanceResponse)
async def consume_inventory(
    item_id: uuid.UUID,
    body: ConsumeRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be positive")
    item = await _get_item_or_404(db, current.org_id, item_id)
    _reject_lot_tracked(item)

    new_on_hand = await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
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
        metadata={"quantity": str(body.quantity), "reason": body.reason},
    )
    await db.commit()
    return _balance_response(item, new_on_hand)


@router.post("/{item_id}/adjust", response_model=InventoryBalanceResponse)
async def adjust_inventory(
    item_id: uuid.UUID,
    body: AdjustRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be a non-zero correction")
    item = await _get_item_or_404(db, current.org_id, item_id)
    _reject_lot_tracked(item)

    new_on_hand = await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=item_id,
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
        metadata={"delta": str(body.quantity), "reason": body.reason},
    )
    await db.commit()
    return _balance_response(item, new_on_hand)
