from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkCenter(Base):
    """A routing step type (e.g. "CNC Milling") - the physical/logical station
    a Routing's steps point at. Distinct from Machine: a work center can have
    several interchangeable Machines (machine_capacity), so one broken
    machine doesn't take the whole station down for simulation/queueing
    purposes."""

    __tablename__ = "work_centers"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_work_centers_org_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # active | inactive | retired - not an enum column so a new status later
    # doesn't need a migration, validated in the route instead (matches Item.item_type).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    machine_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    default_cycle_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    default_mtbf_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    default_mttr_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
