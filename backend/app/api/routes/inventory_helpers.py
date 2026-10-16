"""Schemas and helpers for the /inventory routes (split out of inventory.py)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_404
from app.models.bom import Item
from app.models.inventory import InventoryAdjustmentRequest, InventoryBalance
from app.models.location import Location
from app.models.tenant import Org
from app.services.inventory import apply_inventory_delta


ReasonCode = Literal["cycle_count", "damage", "found_stock", "shrinkage", "correction", "other"]


class _ReasonFields(BaseModel):
    reason_code: ReasonCode
    reason_note: str | None = None

    @model_validator(mode="after")
    def _note_required_for_other(self) -> "_ReasonFields":
        if self.reason_code == "other" and not self.reason_note:
            raise ValueError("reason_note is required when reason_code is 'other'")
        return self


class AdjustRequest(_ReasonFields):
    # Signed correction: +3 found more on the shelf, -3 found fewer. A cycle
    # count is entered as the delta from the system figure, not the new
    # absolute - that keeps the write a single guarded increment with no
    # read-modify-write race (see services.inventory.apply_inventory_delta).
    quantity: Decimal
    location_id: str | None = None


class CountRequest(_ReasonFields):
    location_id: str | None = None
    # A physical count can't be negative - unlike AdjustRequest.quantity, which
    # is a signed delta and legitimately goes either way.
    counted_quantity: Decimal = Field(ge=0)
    reason_code: ReasonCode = "cycle_count"


class ApproveRejectRequest(BaseModel):
    review_note: str | None = None


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
    counted_quantity: Decimal | None
    expected_quantity: Decimal | None
    reason_note: str | None
    created_by: str
    created_at: datetime


class InventoryDetailResponse(InventoryItemResponse):
    transactions: list[InventoryTransactionResponse]


class AdjustmentRequestResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    location_id: str
    location_code: str
    quantity_delta: Decimal
    counted_quantity: Decimal | None
    expected_quantity_at_submit: Decimal
    reason_code: str
    reason_note: str | None
    status: str
    requested_by: str
    requested_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None


class AdjustmentOutcome(BaseModel):
    applied: bool
    item: InventoryItemResponse | None = None
    request: AdjustmentRequestResponse | None = None


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


async def _current_on_hand(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, location_id: uuid.UUID
) -> Decimal:
    return (
        await db.execute(
            select(InventoryBalance.quantity_on_hand).where(
                InventoryBalance.org_id == org_id,
                InventoryBalance.item_id == item_id,
                InventoryBalance.location_id == location_id,
            )
        )
    ).scalar_one_or_none() or Decimal("0.0000")


def _adjustment_request_response(
    req: InventoryAdjustmentRequest, item: Item, location: Location
) -> AdjustmentRequestResponse:
    return AdjustmentRequestResponse(
        id=str(req.id),
        item_id=str(item.id),
        item_sku=item.sku,
        location_id=str(location.id),
        location_code=location.code,
        quantity_delta=req.quantity_delta,
        counted_quantity=req.counted_quantity,
        expected_quantity_at_submit=req.expected_quantity_at_submit,
        reason_code=req.reason_code,
        reason_note=req.reason_note,
        status=req.status,
        requested_by=str(req.requested_by),
        requested_at=req.requested_at,
        reviewed_by=str(req.reviewed_by) if req.reviewed_by else None,
        reviewed_at=req.reviewed_at,
        review_note=req.review_note,
    )


async def _post_or_queue_adjustment(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    role: str,
    item: Item,
    location_id: uuid.UUID,
    delta: Decimal,
    current_on_hand: Decimal,
    reason_code: str,
    reason_note: str | None,
    counted_quantity: Decimal | None,
    created_by: uuid.UUID,
) -> AdjustmentOutcome:
    """The single place that decides "apply now" vs "queue for approval",
    shared by /adjust and /count. Owner never queues (owner is the approver).
    Manager queues only when |delta| exceeds the org's threshold."""
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
    within_threshold = abs(delta) <= org.adjust_approval_threshold

    if role == "owner" or within_threshold:
        await apply_inventory_delta(
            db,
            org_id=org_id,
            item_id=item.id,
            location_id=location_id,
            delta=delta,
            transaction_type="adjustment",
            created_by=created_by,
            reason=reason_code,
            reason_note=reason_note,
            counted_quantity=counted_quantity,
            expected_quantity=current_on_hand if counted_quantity is not None else None,
        )
        return AdjustmentOutcome(
            applied=True, item=_item_response(item, await _by_location(db, org_id, item.id))
        )

    request = InventoryAdjustmentRequest(
        org_id=org_id,
        item_id=item.id,
        location_id=location_id,
        quantity_delta=delta,
        counted_quantity=counted_quantity,
        expected_quantity_at_submit=current_on_hand,
        reason_code=reason_code,
        reason_note=reason_note,
        requested_by=created_by,
    )
    db.add(request)
    await db.flush()
    location = await get_or_404(db, Location, org_id, location_id, "Location not found")
    return AdjustmentOutcome(applied=False, request=_adjustment_request_response(request, item, location))
