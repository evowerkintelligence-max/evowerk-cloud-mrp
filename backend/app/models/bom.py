from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("org_id", "sku", name="uq_items_org_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # raw_material | component | finished_good - not an enum column so adding a
    # new type later doesn't need a migration, validated in the route instead.
    item_type: Mapped[str] = mapped_column(String(50), nullable=False, default="component")
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False, default="ea")
    # Opt-in: only lot-tracked items require/get a Lot on PO receipt - see
    # app.api.routes.purchase_orders.receive_purchase_order.
    is_lot_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Bom(Base):
    __tablename__ = "boms"
    __table_args__ = (UniqueConstraint("org_id", "item_id", "version", name="uq_boms_org_item_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The item this BOM produces - not the same as a line's component_item_id.
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Server-computed (max version for this item_id + 1) at creation time, never
    # user-supplied - see app.api.routes.boms.create_bom.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # draft | active | archived. One-active-per-item is enforced in the activate
    # route (one transaction), not a DB constraint - see that route for why.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
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


class BomLine(Base):
    __tablename__ = "bom_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bom_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    # Decimal, not float - this feeds cost rollups later, a float would be a
    # correctness bug waiting to happen. No unit_of_measure column here on
    # purpose - always read it from the joined Item instead, so a line can
    # never disagree with its own component's base unit.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
