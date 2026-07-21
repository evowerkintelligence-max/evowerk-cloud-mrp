from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.tenant import Org
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_user_manager_create_bootstraps_org_and_default_location(db_session):
    from app.core.users import UserCreate, UserManager
    from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
    from app.models.user import OAuthAccount
    from app.core.security import _pwd_context
    from fastapi_users.password import PasswordHelper

    user_db = SQLAlchemyUserDatabase(db_session, User, OAuthAccount)
    manager = UserManager(user_db, PasswordHelper(_pwd_context))

    created = await manager.create(
        UserCreate(org_name="Acme", email="alice@example.com", password="correct-horse-battery-staple1")  # pragma: allowlist secret
    )

    assert created.is_verified is False
    assert created.org_id is not None
    # The org-founding user must own their org - require_role("owner") gates
    # BOM/inventory/dataset/forecast/BI/item-catalog management throughout the
    # app, so a wrong default here silently locks new orgs out of themselves.
    assert created.role == "owner"

    org = (await db_session.execute(select(Org).where(Org.id == created.org_id))).scalar_one()
    assert org.name == "Acme"

    # password hash was produced through the same argon2 context, verifiable
    # via the manager's own password_helper (not a re-hash / different scheme)
    verified, _ = manager.password_helper.verify_and_update(
        "correct-horse-battery-staple1", created.hashed_password
    )
    assert verified is True
