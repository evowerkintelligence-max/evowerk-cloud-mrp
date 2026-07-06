from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

settings = get_settings()


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_access_token(*, user_id: str, org_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "org_id": org_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are high-entropy random strings (not user-chosen), sha256 is
    # sufficient here and avoids the cost of argon2 on every refresh lookup.
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account_name: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=account_name, issuer_name=settings.totp_issuer
    )


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)
