from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item

router = APIRouter(prefix="/items", tags=["items"])

VALID_ITEM_TYPES = {"raw_material", "component", "finished_good"}


class ItemCreateRequest(BaseModel):
    sku: str
    name: str
    item_type: str = "component"
    unit_of_measure: str = "ea"


class ItemResponse(BaseModel):
    id: str
    sku: str
    name: str
    item_type: str
    unit_of_measure: str


def _to_response(item: Item) -> ItemResponse:
    return ItemResponse(
        id=str(item.id),
        sku=item.sku,
        name=item.name,
        item_type=item.item_type,
        unit_of_measure=item.unit_of_measure,
    )


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: ItemCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.item_type not in VALID_ITEM_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"item_type must be one of {sorted(VALID_ITEM_TYPES)}")

    item = Item(
        org_id=current.org_id,
        sku=body.sku,
        name=body.name,
        item_type=body.item_type,
        unit_of_measure=body.unit_of_measure,
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
