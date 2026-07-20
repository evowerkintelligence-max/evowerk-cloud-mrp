from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Machine(Base):
    """A physical unit within a WorkCenter (e.g. "CNC-01"). mtbf_hours/
    mttr_hours override the parent WorkCenter's defaults when set.
    needs_review is set True by the backfill migration (see
    registry_matching.py) when this row was auto-created from a free-text
    value that was only a fuzzy, not exact, match to another - surfaced in
    the list UI as a cleanup queue, not a blocker."""

    __tablename__ = "machines"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_machines_org_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_center_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_centers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    mtbf_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    mttr_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
