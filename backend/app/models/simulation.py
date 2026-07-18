from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Persistence for app.services.simulation_engine's what-if calculator: a saved
# baseline + default changes, re-run on demand. Replaces the retired
# what_if_scenarios feature (an arbitrary saved dataset query, redundant with
# the Query tab / BI chart builder) with persistence on top of the engine
# that actually encodes manufacturing domain logic. Distinct from
# app/services/scenario.py (an unrelated inventory lead-time comparison) and
# from app/models/dashboard.py's Chart (a saved BI query).


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    # The run_whatif() baseline parameters (shifts_per_day, oee_pct, ...).
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Default changes applied at run time when a run doesn't supply its own.
    changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SimulationRun(Base):
    """One execution of a Simulation with specific changes - kept as
    history, same shape as WorkflowRun."""

    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The changes actually used for this run (simulation.changes merged with
    # any override passed to POST /simulation/{id}/run).
    changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Full run_whatif() output: {scenario_name, baseline, projected, delta, insights}.
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
