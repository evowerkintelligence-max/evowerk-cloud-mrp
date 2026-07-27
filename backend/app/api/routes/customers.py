from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.sales import Customer, SalesOrder

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerCreateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


class CustomerUpdateRequest(CustomerCreateRequest):
    pass


class CustomerResponse(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None
    is_active: bool


def _to_response(customer: Customer) -> CustomerResponse:
    return CustomerResponse(
        id=str(customer.id),
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        is_active=customer.is_active,
    )


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreateRequest,
    # Broader than suppliers, which is owner-only: raising an order is a
    # shop-floor supervisor action, and a supervisor who cannot add the
    # customer cannot raise the order.
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    customer = Customer(
        org_id=current.org_id, name=body.name, email=body.email, phone=body.phone
    )
    db.add(customer)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"customer {body.name} already exists"
        ) from None

    await log_event(
        db,
        action="customer.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(customer.id),
        metadata={"name": customer.name},
    )
    await db.commit()
    await db.refresh(customer)
    return _to_response(customer)


@router.get("", response_model=list[CustomerResponse])
async def list_customers(
    q: str | None = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Customer).where(Customer.org_id == current.org_id)
    if q:
        query = query.where(Customer.name.ilike(f"%{q}%"))
    if active_only:
        query = query.where(Customer.is_active.is_(True))
    query = query.order_by(Customer.name).limit(limit).offset(offset)
    result = await db.execute(query)
    return [_to_response(customer) for customer in result.scalars().all()]


@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdateRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    customer = await get_or_404(db, Customer, current.org_id, customer_id, "Customer not found")
    customer.name = body.name
    customer.email = body.email
    customer.phone = body.phone
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"customer {body.name} already exists"
        ) from None

    await log_event(
        db,
        action="customer.updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(customer.id),
        metadata={"name": customer.name},
    )
    await db.commit()
    await db.refresh(customer)
    return _to_response(customer)


async def _set_active(
    customer_id: uuid.UUID, is_active: bool, current: CurrentUser, db: AsyncSession
) -> CustomerResponse:
    customer = await get_or_404(db, Customer, current.org_id, customer_id, "Customer not found")
    if not is_active:
        # Deactivating drops the customer out of the order-entry picker while
        # their open orders keep counting as demand, which leaves a live
        # commitment attached to a customer nobody can select. Close the
        # orders first (complete or cancel), then deactivate.
        open_orders = await db.scalar(
            select(func.count(SalesOrder.id)).where(
                SalesOrder.org_id == current.org_id,
                SalesOrder.customer_id == customer_id,
                SalesOrder.status == "open",
            )
        )
        if open_orders:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{customer.name} has {open_orders} open order(s), close them first",
            )
    customer.is_active = is_active
    await db.flush()
    await log_event(
        db,
        action="customer.activated" if is_active else "customer.deactivated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(customer.id),
        metadata={"name": customer.name},
    )
    await db.commit()
    await db.refresh(customer)
    return _to_response(customer)


# No delete endpoint on purpose: deactivation only, so a past order never
# points at a customer row that is gone.
@router.post("/{customer_id}/deactivate", response_model=CustomerResponse)
async def deactivate_customer(
    customer_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_active(customer_id, False, current, db)


@router.post("/{customer_id}/reactivate", response_model=CustomerResponse)
async def reactivate_customer(
    customer_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_active(customer_id, True, current, db)
