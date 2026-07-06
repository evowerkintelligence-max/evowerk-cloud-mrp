from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, CurrentUser
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    generate_totp_secret,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.tenant import Org
from app.models.user import RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    org_name: str
    email: EmailStr
    password: str


class RegisterResponse(BaseModel):
    org_id: uuid.UUID
    user_id: uuid.UUID


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    mfa_required: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    org = Org(name=body.org_name)
    db.add(org)
    await db.flush()

    user = User(
        org_id=org.id,
        email=body.email,
        password_hash=hash_password(body.password),
        role="owner",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await log_event(
        db,
        action="user.register",
        org_id=org.id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    return RegisterResponse(org_id=org.id, user_id=user.id)


async def _issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    access_token = create_access_token(user_id=str(user.id), org_id=str(user.org_id))
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            org_id=user.org_id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_token_expiry(),
        )
    )
    await db.commit()
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        await log_event(
            db,
            action="user.login_failed",
            target=body.email,
            ip_address=_client_ip(request),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.mfa_enabled:
        if not body.totp_code:
            return TokenResponse(access_token="", refresh_token="", mfa_required=True)
        if not verify_totp(user.mfa_secret or "", body.totp_code):
            await log_event(
                db,
                action="user.login_mfa_failed",
                org_id=user.org_id,
                actor_user_id=user.id,
                ip_address=_client_ip(request),
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    tokens = await _issue_tokens(db, user)
    await log_event(
        db,
        action="user.login",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    return tokens


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def mfa_enroll(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    secret = generate_totp_secret()
    result = await db.execute(select(User).where(User.id == current.id))
    user = result.scalar_one()
    user.mfa_secret = secret
    user.mfa_enabled = False
    await db.commit()

    return MfaEnrollResponse(
        secret=secret,
        provisioning_uri=totp_provisioning_uri(secret, user.email),
    )


@router.post("/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == current.id))
    user = result.scalar_one()
    if not user.mfa_secret or not verify_totp(user.mfa_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code")

    user.mfa_enabled = True
    await db.commit()
    await log_event(
        db,
        action="user.mfa_enabled",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if stored is None or stored.revoked:
        raise invalid
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        # SQLite (used in tests) drops tzinfo on round-trip; Postgres preserves it.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise invalid

    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise invalid

    stored.revoked = True
    await db.commit()

    return await _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None:
        stored.revoked = True
        await db.commit()
        await log_event(db, action="user.logout", org_id=stored.org_id, actor_user_id=stored.user_id)
