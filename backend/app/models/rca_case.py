from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Ported from the desktop app's rca.py/rca_engine.py/rca_store.py - a
# persistent investigation workflow layered on top of the dataset-driven OEE
# math that already lives in app.analytics.oee_calc (see app/services/rca_cases.py).
# Distinct from the existing app/services/rca.py (an inventory-stockout
# chi-square check, unrelated feature that happens to share the "RCA" name).


class RcaCase(Base):
    """An investigation: problem statement + status + link to the dataset it's
    computed against. Evidence/Pareto/timeline are computed on demand, not
    stored - only the case, its cause tree, and its actions persist."""

    __tablename__ = "rca_cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    problem_statement: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    # open | investigating | actions_open | closed | verified
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)


class RcaNode(Base):
    """One node of a case's cause tree (fishbone-style). The root "problem"
    node is auto-seeded when a case is created."""

    __tablename__ = "rca_nodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rca_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rca_nodes.id", ondelete="CASCADE"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    # problem | cause | root_cause
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="cause")
    # hypothesis | verified | ruled_out
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="hypothesis")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # [{"kind": "candidate"|"pareto"|"manual", "summary": str, "data": {...}}]
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class RcaAction(Base):
    """A CAPA action, optionally linked to a root-cause node. `verification`
    is populated once by POST .../verify (before/after OEE split at
    completed_at) and cleared if the action is reopened."""

    __tablename__ = "rca_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rca_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rca_nodes.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # open | in_progress | done | verified
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class RcaEvent(Base):
    """Append-only audit trail per case - same shape as WorkflowRunEvent."""

    __tablename__ = "rca_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rca_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
