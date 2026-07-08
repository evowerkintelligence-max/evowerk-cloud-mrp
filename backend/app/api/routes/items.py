from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.procurement import Supplier

router = APIRouter(prefix="/items", tags=["items"])

VALID_ITEM_TYPES = {"raw_material", "component", "finished_good"}


class ItemCreateRequest(BaseModel):
    sku: str
    name: str
    item_type: str = "component"
    unit_of_measure: str = "ea"
    is_lot_tracked: bool = False
    preferred_supplier_id: str | None = None


class ItemUpdateRequest(BaseModel):
    # Always explicit: null clears the preference. This is the only mutable
    # field for now (sku/type/lot-tracking are load-bearing for history and
    # deliberately fixed after creation).
    preferred_supplier_id: str | None


class ItemResponse(BaseModel):
    id: str
    sku: str
    name: str
    item_type: str
    unit_of_measure: str
    is_lot_tracked: bool
    preferred_supplier_id: str | None


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


async def _get_item_or_404(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> Item:
    item = (
        await db.execute(select(Item).where(Item.id == item_id, Item.org_id == org_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: ItemCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.item_type not in VALID_ITEM_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"item_type must be one of {sorted(VALID_ITEM_TYPES)}")
    preferred_supplier_id = await _resolve_supplier_id(db, current.org_id, body.preferred_supplier_id)

    item = Item(
        org_id=current.org_id,
        sku=body.sku,
        name=body.name,
        item_type=body.item_type,
        unit_of_measure=body.unit_of_measure,
        is_lot_tracked=body.is_lot_tracked,
        preferred_supplier_id=preferred_supplier_id,
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "sku already exists in this org") from None

    await log_event(
        db,
        action="item.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item.id),
        metadata={"sku": item.sku},
    )
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.get("", response_model=list[ItemResponse])
async def list_items(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Item).where(Item.org_id == current.org_id).order_by(Item.sku))
    return [_to_response(item) for item in result.scalars().all()]


@router.patch("/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: uuid.UUID,
    body: ItemUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_item_or_404(db, current.org_id, item_id)
    item.preferred_supplier_id = await _resolve_supplier_id(
        db, current.org_id, body.preferred_supplier_id
    )
    await db.flush()

    await log_event(
        db,
        action="item.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item.id),
        metadata={"preferred_supplier_id": body.preferred_supplier_id},
    )
    await db.commit()
    return _to_response(item)
