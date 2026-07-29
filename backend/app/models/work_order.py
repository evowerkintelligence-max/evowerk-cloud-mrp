from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkOrder(Base):
    """One production job: make N of an item. The execution record the app
    was missing, as opposed to Bom and Routing which describe how a thing is
    made in the abstract.
    """

    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("org_id", "wo_number", name="uq_wos_org_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True
    )
    # The recipe VERSION in force when the job was raised or last edited.
    # Snapshotted rather than looked up live so editing a BOM next month
    # cannot rewrite what this job reserved or consumed.
    bom_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("boms.id"), nullable=False)
    # Recorded because the definition exists and is useful history. This
    # phase does not track the job step by step.
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("routings.id"), nullable=True
    )
    # Who's actually running this job - a plan-level fact, not an
    # observation. Nullable: most jobs never set it. Participates only in
    # resolve_attribution's fallback query (app.services.operator_capture);
    # no capture-event history of its own, because a work order's assignment
    # can change after the fact and this column has to reflect that.
    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("operators.id"), nullable=True
    )
    # Server-computed (max wo_number for this org + 1) at creation, never
    # user-supplied. Plain int; format it as "WO-4" at the response or
    # frontend layer, never store the formatted string.
    wo_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # scheduled | in_progress | done | cancelled. Only `scheduled` and
    # `in_progress` reach the planning engine, and only `scheduled` holds
    # stock - see app.services.work_orders.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled", index=True)
    quantity_planned: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity_good: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    quantity_scrapped: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal(0)
    )
    # A scrap count with no cause tells the shop its yield is poor without
    # telling it why, which is the question the number always prompts next.
    scrap_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # not_started | consumed. What happened to the components of units never
    # attempted. Null unless the job finished below its planned quantity.
    remainder_disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id"), nullable=False
    )
    # When the shop intends to begin. Drives both the planning horizon bound
    # and the reservation ranking: the job due soonest wins contested stock.
    scheduled_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WorkOrderComponent(Base):
    """What this job takes. Stored rather than re-derived from the BOM,
    because the BOM may change between raising and starting a job and both
    the reservation and the return-on-cancel have to reflect what this job
    actually committed to.
    """

    __tablename__ = "work_order_components"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False)
    # BOM quantity times planned quantity, already including that BOM line's
    # own scrap_pct (load_bom_children folds it in).
    quantity_required: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity_consumed: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal(0)
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkOrderComponentLot(Base):
    """Which lots a lot-tracked component was actually drawn from, written
    from the LotConsumption list consume_lot_fefo returns. It exists so a
    cancel or an under-run return can put stock back in the lots it came out
    of; LotTransaction carries no job reference, so the split cannot be
    recovered from the existing ledger.
    """

    __tablename__ = "work_order_component_lots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wo_component_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_order_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("lots.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
