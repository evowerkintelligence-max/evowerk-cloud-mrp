# backend/app/models/capture_event.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CaptureEvent(Base):
    """One row per who-was-at-this-machine signal, from a badge/RFID reader
    (source="reader") or a QR check-in tap (source="qr"). A machine has at
    most one open event (ended_at is null) at a time - the next scan on that
    machine closes it before opening its own, matching PaceSession's own
    start/stop simplicity, minus the throughput-tap machinery. See
    app.services.operator_capture. Work-order assignment (the third,
    weakest tier) produces no row here at all - see WorkOrder.assigned_operator_id
    and resolve_attribution's query-time fallback."""

    __tablename__ = "capture_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
