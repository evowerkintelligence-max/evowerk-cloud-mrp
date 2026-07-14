from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Ported from the desktop app's predictive.py ScheduleItem, moved from a JSON
# file store to a real org-scoped table (same reasoning as every other
# desktop JSON-store port in this app).


class MaintenanceSchedule(Base):
    __tablename__ = "maintenance_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    # immediate | scheduled | monitor
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    action: Mapped[str] = mapped_column(String(2000), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    scheduled_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # pending | in_progress | done | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    pm_hours: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)
    notes: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
