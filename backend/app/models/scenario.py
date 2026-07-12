from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Ported from the desktop app's scenarios.py/scenarios_store.py: a saved
# query (DatasetQueryRequest-shaped, see app.services.dataset_query) plus
# default parameter overrides, re-run on demand. Distinct from the existing
# app/services/scenario.py (an unrelated inventory lead-time comparison that
# happens to share the name) and from Simulation (a fixed-formula capacity/
# cost model, not an arbitrary saved query).


class Scenario(Base):
    __tablename__ = "what_if_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    # A DatasetQueryRequest dict (select/group_by/filters/order_by/limit).
    base_query: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Default shallow-merge overrides applied on top of base_query at execute
    # time when the caller doesn't supply its own param_sets.
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ScenarioRun(Base):
    """One execution of a Scenario with a specific set of parameter
    overrides - kept as history, same shape as WorkflowRun."""

    __tablename__ = "what_if_scenario_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("what_if_scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
