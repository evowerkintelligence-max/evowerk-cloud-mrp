from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RealtimeStream(Base):
    """A tenant-scoped definition of a live-polling collector over an
    Integration, ported from the desktop app's realtime/engine.py.

    The desktop app kept stream state purely in memory (a dict of running
    background threads) - fine for one always-on local process, but this
    server can restart or redeploy. This row is the durable half: the
    collector loop itself is still an in-process thread (see
    app.services.realtime.StreamRunner), but its definition and
    `desired_status` survive a restart, and app.main's startup hook resumes
    any stream still marked "running" so a redeploy doesn't silently lose it.

    `id` doubles as the stream_id used for the SSE route and the on-disk tick
    history file (app.services.realtime.history_path), so a resumed stream
    keeps its history contiguous.
    """

    __tablename__ = "realtime_streams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # {"units_col": "...", "scrap_col": "...", ...} - see StreamRunner.snapshot for the keys read.
    column_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    accumulate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # running | stopped - the user's intent, used to decide what to resume on startup.
    # The *live* status (running/error, from the in-memory collector thread) is
    # ephemeral and only available via GET .../snapshot while this process is up.
    desired_status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
