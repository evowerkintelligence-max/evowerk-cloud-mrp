from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import _pwd_context
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.location import Location
from app.models.tenant import Org
from app.models.user import OAuthAccount, User

settings = get_settings()


class UserCreate(schemas.BaseUserCreate):
    org_name: str

    @field_validator("password")
    @classmethod
    def password_policy(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(self, user_db: SQLAlchemyUserDatabase, password_helper: PasswordHelper) -> None:
        super().__init__(user_db, password_helper)
        self.reset_password_token_secret = settings.jwt_secret_key
        self.verification_token_secret = settings.jwt_secret_key

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        existing = await self.user_db.get_by_email(user_create.email)
        if existing is not None:
            raise exceptions.UserAlreadyExists()

        session = self.user_db.session

        user_dict = (
            user_create.create_update_dict() if safe else user_create.create_update_dict_superuser()
        )
        org_name = user_dict.pop("org_name")
        password = user_dict.pop("password")
        user_dict["hashed_password"] = self.password_helper.hash(password)
        user_dict["is_verified"] = False

        org = Org(name=org_name)
        session.add(org)
        await session.flush()
        # Every org starts with one default location so the location dimension is
        # optional: receive/consume/adjust fall back to it until the user creates
        # more (see app.services.inventory / app.api.routes.inventory).
        session.add(Location(org_id=org.id, code="MAIN", name="Main", is_default=True))
        user_dict["org_id"] = org.id

        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.register",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from app.core.email import send_password_reset_email

        session = self.user_db.session
        await send_password_reset_email(user.email, token)
        await log_event(
            session,
            action="user.password_reset_requested",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.password_reset",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from app.core.email import send_verification_email

        session = self.user_db.session
        await send_verification_email(user.email, token)
        await log_event(
            session,
            action="user.verification_requested",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()

    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        session = self.user_db.session
        await log_event(
            session,
            action="user.verified",
            org_id=user.org_id,
            actor_user_id=user.id,
            ip_address=_client_ip(request),
        )
        await session.commit()


async def get_user_db(session: AsyncSession = Depends(get_db)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db, PasswordHelper(_pwd_context))


bearer_transport = BearerTransport(tokenUrl="auth/login")


def _get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.jwt_secret_key, lifetime_seconds=settings.access_token_expire_minutes * 60)


# Only used to satisfy FastAPIUsers' constructor (register/reset/verify
# routers don't need it) - /auth/login stays a custom route (see auth.py)
# because MFA gating and refresh-token issuance need a response shape this
# backend's default login router doesn't produce.
auth_backend = AuthenticationBackend(name="jwt", transport=bearer_transport, get_strategy=_get_jwt_strategy)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
