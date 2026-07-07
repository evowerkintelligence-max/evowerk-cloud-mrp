from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, CurrentUser
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    generate_refresh_token,
    generate_totp_secret,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    totp_provisioning_uri,
    verify_password,
    verify_password_timing_safe,
    verify_totp,
)
from app.db.session import get_db
from app.middleware.audit import log_event
from app.middleware.rate_limit import rate_limit
from app.models.tenant import Org
from app.models.user import RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

_register_rate_limit = rate_limit("register", settings.rate_limit_register_per_hour, 3600)
_login_rate_limit = rate_limit("login", settings.rate_limit_login_per_5min, 300)
_refresh_rate_limit = rate_limit("refresh", settings.rate_limit_refresh_per_5min, 300)
_mfa_verify_rate_limit = rate_limit("mfa_verify", settings.rate_limit_mfa_verify_per_5min, 300)


class RegisterRequest(BaseModel):
    org_name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_policy(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


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


class MfaEnrollRequest(BaseModel):
    # Required to (re-)start enrollment on an account that already has MFA enabled,
    # so a stolen/short-lived access token alone can't be used to swap out the secret.
    password: str | None = None


class MfaEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _is_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def _register_failed_attempt(user: User) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.max_failed_login_attempts:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.account_lockout_minutes
        )
        user.failed_login_attempts = 0


def _reset_failed_attempts(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_register_rate_limit)],
)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
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
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None

    await log_event(
        db,
        action="user.register",
        org_id=org.id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    await db.commit()
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
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_login_rate_limit)])
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None:
        # Always pay the argon2 cost, even with no real hash to check, so this path
        # can't be timed against the "wrong password" path to enumerate emails.
        verify_password_timing_safe(body.password, None)
        await log_event(db, action="user.login_failed", target=body.email, ip_address=_client_ip(request))
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if _is_locked(user):
        await log_event(
            db,
            action="user.login_locked_out",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts, try again later")

    if not verify_password_timing_safe(body.password, user.password_hash):
        _register_failed_attempt(user)
        await log_event(
            db,
            action="user.login_failed",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not user.is_active:
        await log_event(
            db,
            action="user.login_disabled",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    if user.mfa_enabled:
        if not body.totp_code:
            await db.commit()  # nothing changed, but keep session hygiene consistent
            return TokenResponse(access_token="", refresh_token="", mfa_required=True)
        if not verify_totp(decrypt_secret(user.mfa_secret or ""), body.totp_code):
            _register_failed_attempt(user)
            await log_event(
                db,
                action="user.login_mfa_failed",
                org_id=user.org_id,
                actor_user_id=user.id,
                ip_address=_client_ip(request),
            )
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    _reset_failed_attempts(user)
    tokens = await _issue_tokens(db, user)
    await log_event(
        db,
        action="user.login",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return tokens


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def mfa_enroll(
    body: MfaEnrollRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == current.id))
    user = result.scalar_one()

    if user.mfa_enabled:
        if not body.password or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Password confirmation required")

    secret = generate_totp_secret()
    # Stored as pending only - mfa_secret/mfa_enabled are untouched until /mfa/verify
    # succeeds, so calling enroll alone can never disable or replace active MFA.
    user.pending_mfa_secret = encrypt_secret(secret)
    await db.commit()

    return MfaEnrollResponse(
        secret=secret,
        provisioning_uri=totp_provisioning_uri(secret, user.email),
    )


@router.post(
    "/mfa/verify",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_mfa_verify_rate_limit)],
)
async def mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == current.id))
    user = result.scalar_one()
    if not user.pending_mfa_secret or not verify_totp(decrypt_secret(user.pending_mfa_secret), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code")

    user.mfa_secret = user.pending_mfa_secret
    user.pending_mfa_secret = None
    user.mfa_enabled = True
    await log_event(
        db,
        action="user.mfa_enabled",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
    )
    await db.commit()


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(_refresh_rate_limit)])
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
    if user is None or not user.is_active:
        raise invalid

    stored.revoked = True
    tokens = await _issue_tokens(db, user)
    await db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None:
        stored.revoked = True
        await log_event(db, action="user.logout", org_id=stored.org_id, actor_user_id=stored.user_id)
        await db.commit()
