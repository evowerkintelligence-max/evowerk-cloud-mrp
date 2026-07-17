# backend/app/models/downtime_tracking.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DowntimeIncident(Base):
    """One machine-down-to-machine-up event, reported live from the downtime
    board. Closing an incident (setting ended_at) re-materializes every
    closed incident for the org into that org's DowntimeLiveDataset - see
    app.services.downtime_tracking.end_incident - so the existing Downtime
    and Shifts analytics pages work on live-captured data with zero changes.
    """

    __tablename__ = "downtime_incidents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    # breakdown | changeover | material_shortage | planned_maintenance | quality_hold | other
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    shift: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DowntimeLiveDataset(Base):
    """One row per org - points at the perpetually-updated Dataset that
    end_incident() keeps overwriting with every closed incident to date."""

    __tablename__ = "downtime_live_datasets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
