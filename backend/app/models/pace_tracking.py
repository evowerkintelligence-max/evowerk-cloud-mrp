# backend/app/models/pace_tracking.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaceSession(Base):
    """A live tap-tracking session for a manual/assembly line with no sensors or
    time-tracking system - the only data source is an operator tapping a button
    per completed unit. On Stop, this materializes into a normal, immutable
    Dataset row via the same ingestion path a CSV upload uses (see
    app.services.pace_tracking.stop_session), so every OEE engine feature works
    on pace-tracking data with zero changes.
    """

    __tablename__ = "pace_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    line: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    target_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # active | paused | stopped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # breakdown | setup | other - only meaningful while status == "paused"
    pause_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Accumulators, updated atomically in-SQL by
    # app.services.pace_tracking._finalize_pause so two concurrent increments
    # to the same column always sum correctly. That does not guard the
    # status transition itself (no row lock) - see _finalize_pause's docstring.
    unplanned_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    planned_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    other_dt_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id"), nullable=True
    )


class PaceTap(Base):
    """One tap event - the raw audit/debug log. The session row's accumulators
    plus COUNT(*)-by-kind on this table are what materialization actually reads."""

    __tablename__ = "pace_taps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pace_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # good | reject
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
