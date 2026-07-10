from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Location(Base):
    """A physical place stock is held - a warehouse, stockroom, or bin. On-hand
    for non-lot-tracked items is tracked per (org, item, location) so a plant
    can see and move stock between locations (see inventory_balances). Every org
    gets exactly one is_default=True location at registration; consume/adjust/
    receive fall back to it when the caller doesn't name a location, which keeps
    the location dimension optional for simple single-site users.
    """

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_locations_org_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Short human handle, unique per org (e.g. "MAIN", "WH2", "LINE-1").
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Exactly one per org. Uniqueness of the default is enforced in the
    # registration/creation logic, not a DB constraint (a partial unique index
    # is awkward across SQLite/Postgres) - see app.api.routes.auth.register.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
