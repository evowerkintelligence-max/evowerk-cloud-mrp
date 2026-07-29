# backend/app/models/operator.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Operator(Base):
    """An org-scoped roster of shop-floor operators, mirroring Machine.
    needs_review is set True when a row was created as a fuzzy-close match to
    an existing name (see registry_matching.resolve_or_create_operator) - a
    cleanup queue, not a blocker. code and badge_id are nullable because most
    rows are created by dataset auto-discovery or a QR tap, neither of which
    has an employee ID or badge to record."""

    __tablename__ = "operators"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_name", name="uq_operators_org_normalized_name"),
        # Partial: badge_id is nullable and most rows have none, so a plain
        # unique constraint would let only one badge-less row exist per org.
        Index(
            "uq_operators_org_badge",
            "org_id",
            "badge_id",
            unique=True,
            postgresql_where=text("badge_id IS NOT NULL"),
            sqlite_where=text("badge_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    badge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
