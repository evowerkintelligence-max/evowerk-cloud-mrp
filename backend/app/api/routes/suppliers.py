from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.procurement import Supplier

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierCreateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


class SupplierResponse(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None


def _to_response(supplier: Supplier) -> SupplierResponse:
    return SupplierResponse(
        id=str(supplier.id), name=supplier.name, email=supplier.email, phone=supplier.phone
    )


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    body: SupplierCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    supplier = Supplier(org_id=current.org_id, name=body.name, email=body.email, phone=body.phone)
    db.add(supplier)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Supplier name already exists in this org") from None

    await log_event(
        db,
        action="supplier.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(supplier.id),
        metadata={"name": supplier.name},
    )
    await db.commit()
    await db.refresh(supplier)
    return _to_response(supplier)


@router.get("", response_model=list[SupplierResponse])
async def list_suppliers(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Supplier).where(Supplier.org_id == current.org_id).order_by(Supplier.name)
    )
    return [_to_response(supplier) for supplier in result.scalars().all()]
