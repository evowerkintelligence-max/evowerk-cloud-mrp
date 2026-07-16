# backend/app/models/shift_schedule.py
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShiftSchedule(Base):
    """A shift's weekly-recurring start/end time ("HH:MM", browser-local), per
    org - no per-date overrides, no timezone handling (matches the rest of the
    app). Powers the Shifts page's "current shift" resolution for the
    handover Pareto and the hourly-pacing minutes-left framing."""

    __tablename__ = "shift_schedules"
    __table_args__ = (UniqueConstraint("org_id", "shift_label", name="uq_shift_schedules_org_label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_label: Mapped[str] = mapped_column(String(64), nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"; end < start crosses midnight
