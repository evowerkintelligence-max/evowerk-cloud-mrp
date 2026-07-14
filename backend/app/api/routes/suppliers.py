from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.procurement import Supplier, SupplierContact

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierCreateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


class SupplierUpdateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


class SupplierResponse(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None
    is_active: bool


def _to_response(supplier: Supplier) -> SupplierResponse:
    return SupplierResponse(
        id=str(supplier.id),
        name=supplier.name,
        email=supplier.email,
        phone=supplier.phone,
        is_active=supplier.is_active,
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
    q: str | None = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Supplier).where(Supplier.org_id == current.org_id)
    if q:
        query = query.where(Supplier.name.ilike(f"%{q}%"))
    if active_only:
        query = query.where(Supplier.is_active.is_(True))
    query = query.order_by(Supplier.name).limit(limit).offset(offset)
    result = await db.execute(query)
    return [_to_response(supplier) for supplier in result.scalars().all()]


@router.patch("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: uuid.UUID,
    body: SupplierUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    supplier = await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    supplier.name = body.name
    supplier.email = body.email
    supplier.phone = body.phone
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Supplier name already exists in this org") from None

    await log_event(
        db,
        action="supplier.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(supplier.id),
        metadata={"name": supplier.name},
    )
    await db.commit()
    await db.refresh(supplier)
    return _to_response(supplier)


async def _set_active(
    supplier_id: uuid.UUID, is_active: bool, current: CurrentUser, db: AsyncSession
) -> SupplierResponse:
    supplier = await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    supplier.is_active = is_active
    await db.flush()
    await log_event(
        db,
        action="supplier.activated" if is_active else "supplier.deactivated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(supplier.id),
        metadata={"name": supplier.name},
    )
    await db.commit()
    await db.refresh(supplier)
    return _to_response(supplier)


@router.post("/{supplier_id}/deactivate", response_model=SupplierResponse)
async def deactivate_supplier(
    supplier_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_active(supplier_id, False, current, db)


@router.post("/{supplier_id}/reactivate", response_model=SupplierResponse)
async def reactivate_supplier(
    supplier_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_active(supplier_id, True, current, db)


class SupplierContactRequest(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False


class SupplierContactResponse(BaseModel):
    id: str
    name: str
    role: str | None
    email: str | None
    phone: str | None
    is_primary: bool


def _contact_to_response(contact: SupplierContact) -> SupplierContactResponse:
    return SupplierContactResponse(
        id=str(contact.id),
        name=contact.name,
        role=contact.role,
        email=contact.email,
        phone=contact.phone,
        is_primary=contact.is_primary,
    )


async def _unset_existing_primary(db: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID) -> None:
    await db.execute(
        update(SupplierContact)
        .where(
            SupplierContact.org_id == org_id,
            SupplierContact.supplier_id == supplier_id,
            SupplierContact.is_primary.is_(True),
        )
        .values(is_primary=False)
    )


@router.get("/{supplier_id}/contacts", response_model=list[SupplierContactResponse])
async def list_supplier_contacts(
    supplier_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    result = await db.execute(
        select(SupplierContact)
        .where(SupplierContact.org_id == current.org_id, SupplierContact.supplier_id == supplier_id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.name)
    )
    return [_contact_to_response(c) for c in result.scalars().all()]


@router.post(
    "/{supplier_id}/contacts",
    response_model=SupplierContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_contact(
    supplier_id: uuid.UUID,
    body: SupplierContactRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    if body.is_primary:
        await _unset_existing_primary(db, current.org_id, supplier_id)

    contact = SupplierContact(
        org_id=current.org_id,
        supplier_id=supplier_id,
        name=body.name,
        role=body.role,
        email=body.email,
        phone=body.phone,
        is_primary=body.is_primary,
    )
    db.add(contact)
    await db.flush()

    await log_event(
        db,
        action="supplier.contact_created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact.id),
        metadata={"supplier_id": str(supplier_id), "name": contact.name},
    )
    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.patch("/{supplier_id}/contacts/{contact_id}", response_model=SupplierContactResponse)
async def update_supplier_contact(
    supplier_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: SupplierContactRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    contact = await get_or_404(
        db,
        SupplierContact,
        current.org_id,
        contact_id,
        "Contact not found",
        SupplierContact.supplier_id == supplier_id,
    )
    if body.is_primary and not contact.is_primary:
        await _unset_existing_primary(db, current.org_id, supplier_id)

    contact.name = body.name
    contact.role = body.role
    contact.email = body.email
    contact.phone = body.phone
    contact.is_primary = body.is_primary
    await db.flush()

    await log_event(
        db,
        action="supplier.contact_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact.id),
        metadata={"supplier_id": str(supplier_id), "name": contact.name},
    )
    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.delete("/{supplier_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_contact(
    supplier_id: uuid.UUID,
    contact_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    contact = await get_or_404(
        db,
        SupplierContact,
        current.org_id,
        contact_id,
        "Contact not found",
        SupplierContact.supplier_id == supplier_id,
    )
    await db.delete(contact)
    await log_event(
        db,
        action="supplier.contact_deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact_id),
        metadata={"supplier_id": str(supplier_id)},
    )
    await db.commit()
