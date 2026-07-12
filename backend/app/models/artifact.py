from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Artifact(Base):
    """A tenant-scoped generated file (export, report render, run output).
    The row is metadata only - the bytes live on disk under
    app.services.artifacts._path_for(). Ported from the desktop app's
    artifacts_store.py, replacing its filesystem-JSON index with a real table
    (same reasoning as every other desktop JSON-store -> ORM port in this app).
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Linkage to the thing this artifact was generated from, for filtering in
    # the list endpoint. Deliberately plain strings, not FKs: an artifact can
    # point at a query run, scenario run, or report id, and outlives its
    # source (e.g. a deleted report's export should still be downloadable).
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    report_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
