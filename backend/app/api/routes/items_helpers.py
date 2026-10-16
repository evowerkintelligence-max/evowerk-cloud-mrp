"""Schemas and helpers for the /items routes (split out of items.py)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.audit import log_event
from app.models.bom import Item, ItemUnit
from app.models.procurement import Supplier


VALID_ITEM_TYPES = {"raw_material", "component", "finished_good"}


class ItemCreateRequest(BaseModel):
    sku: str
    name: str
    item_type: str = "component"
    unit_of_measure: str = "ea"
    is_lot_tracked: bool = False
    preferred_supplier_id: str | None = None
    lead_time_days: int | None = None
    min_order_qty: Decimal | None = None
    safety_stock_qty: Decimal | None = None
    description: str | None = None
    barcode: str | None = None


class ItemUpdateRequest(BaseModel):
    # Always explicit: null clears the preference. This is the only
    # required-every-call field (sku/type/lot-tracking are load-bearing for
    # history and deliberately fixed after creation).
    preferred_supplier_id: str | None
    # Optional/omittable, unlike preferred_supplier_id above: existing callers
    # that only ever sent one of these fields shouldn't have the others
    # silently reset. Checked via model_fields_set in update_item.
    lead_time_days: int | None = None
    min_order_qty: Decimal | None = None
    safety_stock_qty: Decimal | None = None
    is_active: bool | None = None
    name: str | None = None
    unit_of_measure: str | None = None
    description: str | None = None
    barcode: str | None = None


class ItemResponse(BaseModel):
    id: str
    sku: str
    name: str
    item_type: str
    unit_of_measure: str
    is_lot_tracked: bool
    preferred_supplier_id: str | None
    lead_time_days: int | None
    min_order_qty: Decimal | None
    safety_stock_qty: Decimal | None
    is_active: bool
    description: str | None
    barcode: str | None


def _to_response(item: Item) -> ItemResponse:
    return ItemResponse(
        id=str(item.id),
        sku=item.sku,
        name=item.name,
        item_type=item.item_type,
        unit_of_measure=item.unit_of_measure,
        is_lot_tracked=item.is_lot_tracked,
        preferred_supplier_id=(
            str(item.preferred_supplier_id) if item.preferred_supplier_id else None
        ),
        lead_time_days=item.lead_time_days,
        min_order_qty=item.min_order_qty,
        safety_stock_qty=item.safety_stock_qty,
        is_active=item.is_active,
        description=item.description,
        barcode=item.barcode,
    )


class ItemUnitCreateRequest(BaseModel):
    code: str
    factor_to_base: Decimal


class ItemUnitResponse(BaseModel):
    id: str
    item_id: str
    code: str
    factor_to_base: Decimal


def _unit_to_response(unit: ItemUnit) -> ItemUnitResponse:
    # Format factor_to_base with 6 decimal places to match Numeric(18, 6) precision
    factor_str = f"{unit.factor_to_base:.6f}"
    return ItemUnitResponse(
        id=str(unit.id), item_id=str(unit.item_id), code=unit.code, factor_to_base=Decimal(factor_str)
    )


async def _resolve_supplier_id(
    db: AsyncSession, org_id: uuid.UUID, raw: str | None
) -> uuid.UUID | None:
    """Parse and validate a preferred_supplier_id belongs to this org, or None
    to clear it."""
    if raw is None:
        return None
    try:
        supplier_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid preferred_supplier_id") from None
    exists = (
        await db.execute(select(Supplier.id).where(Supplier.id == supplier_id, Supplier.org_id == org_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preferred supplier not found")
    return supplier_id


async def _create_item_row(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    sku: str,
    name: str,
    item_type: str = "component",
    unit_of_measure: str = "ea",
    is_lot_tracked: bool = False,
    preferred_supplier_id: str | None = None,
    lead_time_days: int | None = None,
    min_order_qty: Decimal | None = None,
    safety_stock_qty: Decimal | None = None,
    description: str | None = None,
    barcode: str | None = None,
) -> Item:
    """Validate and insert one Item row (flush + audit log, no commit -
    caller commits). Shared by create_item and the CSV import loop, which
    commits after each row rather than once at the end - see import_items."""
    if item_type not in VALID_ITEM_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"item_type must be one of {sorted(VALID_ITEM_TYPES)}")
    if lead_time_days is not None and lead_time_days < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_time_days must be non-negative")
    if min_order_qty is not None and min_order_qty <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "min_order_qty must be positive")
    if safety_stock_qty is not None and safety_stock_qty < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "safety_stock_qty must be non-negative")
    resolved_supplier_id = await _resolve_supplier_id(db, org_id, preferred_supplier_id)

    item = Item(
        org_id=org_id,
        sku=sku,
        name=name,
        item_type=item_type,
        unit_of_measure=unit_of_measure,
        is_lot_tracked=is_lot_tracked,
        preferred_supplier_id=resolved_supplier_id,
        lead_time_days=lead_time_days,
        min_order_qty=min_order_qty,
        safety_stock_qty=safety_stock_qty,
        description=description,
        barcode=barcode,
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "sku or barcode already exists in this org") from None

    await log_event(
        db,
        action="item.created",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target=str(item.id),
        metadata={"sku": item.sku},
    )
    return item
