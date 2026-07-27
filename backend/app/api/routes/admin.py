from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users import exceptions
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import StaffSession, require_staff_session
from app.core.features import FEATURES
from app.core.users import UserCreate, UserManager, get_user_manager
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.tenant import Org
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff_session)])


def _validate_feature_keys(v: list[str]) -> list[str]:
    unknown = [key for key in v if key not in FEATURES]
    if unknown:
        raise ValueError(f"Unknown feature key(s): {', '.join(unknown)}")
    return v


class CustomerCreateRequest(BaseModel):
    org_name: str
    owner_email: EmailStr
    enabled_features: list[str]

    _validate_enabled_features = field_validator("enabled_features")(_validate_feature_keys)


class CustomerResponse(BaseModel):
    org_id: str
    org_name: str
    owner_email: str
    enabled_features: list[str]


class CustomerFeaturesUpdate(BaseModel):
    enabled_features: list[str]

    _validate_enabled_features = field_validator("enabled_features")(_validate_feature_keys)


@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
    current: StaffSession = Depends(require_staff_session),
):
    # Same Org+Location+owner-role creation transaction /auth/register uses
    # (UserManager.create, backend/app/core/users.py) - just with a throwaway
    # password the customer never sees, since forgot_password() below sends
    # them a real "set your password" link instead. org_features is set on
    # the Org inside that same transaction, so a crash after it commits never
    # leaves a customer created with enabled_features stuck at empty.
    # token_urlsafe's 64-char alphabet holds only 10 digits, so ~1 draw in 215
    # comes back with no digit at all - which UserCreate.password_policy then
    # rejects, 500ing this endpoint at random. Append a letter+digit so the
    # throwaway satisfies the policy by construction rather than by luck.
    throwaway_password = secrets.token_urlsafe(24) + "a1"
    try:
        created_user = await user_manager.create(
            UserCreate(
                email=body.owner_email,
                password=throwaway_password,
                org_name=body.org_name,
                org_features=body.enabled_features,
            ),
            safe=True,
            request=request,
        )
    except exceptions.UserAlreadyExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None

    org = (await db.execute(select(Org).where(Org.id == created_user.org_id))).scalar_one()
    await log_event(
        db,
        action="org.features_granted",
        org_id=org.id,
        actor_user_id=current.id,
        target=str(org.id),
        metadata={"enabled_features": org.enabled_features},
    )
    await db.commit()

    # Same flow /auth/forgot-password uses (on_after_forgot_password sends the
    # email via SES/logging - app/core/users.py, app/core/email.py).
    await user_manager.forgot_password(created_user, request)

    return CustomerResponse(
        org_id=str(org.id), org_name=org.name,
        owner_email=body.owner_email, enabled_features=org.enabled_features,
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    db: AsyncSession = Depends(get_db),
    _current: StaffSession = Depends(require_staff_session),
):
    rows = (
        await db.execute(
            select(Org, User.email).outerjoin(
                User, (User.org_id == Org.id) & (User.role == "owner")
            )
        )
    ).all()
    return [
        CustomerResponse(
            org_id=str(org.id), org_name=org.name,
            owner_email=owner_email or "", enabled_features=org.enabled_features,
        )
        for org, owner_email in rows
    ]


@router.patch("/customers/{org_id}", response_model=CustomerResponse)
async def update_customer_features(
    org_id: uuid.UUID,
    body: CustomerFeaturesUpdate,
    db: AsyncSession = Depends(get_db),
    current: StaffSession = Depends(require_staff_session),
):
    # Not app.api.deps.get_or_404: that helper filters on model.org_id, which
    # Org itself doesn't have (Org is the tenant root, not a child row) - this
    # is a direct cross-org lookup by id, which is exactly what a staff-only
    # customer-management endpoint is for.
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    org.enabled_features = body.enabled_features
    await log_event(
        db,
        action="org.features_updated",
        org_id=org.id,
        actor_user_id=current.id,
        target=str(org.id),
        metadata={"enabled_features": body.enabled_features},
    )
    await db.commit()

    owner = (
        await db.execute(select(User).where(User.org_id == org.id, User.role == "owner"))
    ).scalar_one_or_none()
    return CustomerResponse(
        org_id=str(org.id), org_name=org.name,
        owner_email=owner.email if owner else "", enabled_features=org.enabled_features,
    )
