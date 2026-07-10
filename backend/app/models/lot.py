from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "item_id", "lot_number", "location_id", name="uq_lots_org_item_number_location"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True)
    # A lot_number identifies a physical batch, but on-hand is tracked per
    # location: the same batch split across two warehouses is two Lot rows
    # (same lot_number, different location_id), each with its own
    # quantity_remaining - same modelling as InventoryBalance for non-lot-tracked
    # items. Falls back to the org's default location when unspecified.
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lot_number: Mapped[str] = mapped_column(String(100), nullable=False)
    # Cumulative across every receiving event for this lot (a single physical
    # batch is often delivered across multiple partial shipments/POs - see
    # receive_purchase_order's find-or-create-by-lot_number logic).
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    # Current on-hand, decremented by consume(). Source of truth for
    # active/depleted status; LotTransaction below is the durable ledger a
    # running total alone can't provide (who moved how much, when, why).
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    # active | on_hold | depleted. on_hold blocks consume() (a QA
    # hold/recall) regardless of quantity_remaining; depleted is automatic
    # once quantity_remaining hits 0, and reverses if a later receipt tops
    # the lot back up.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class LotTransaction(Base):
    """Append-only ledger of every quantity change against a Lot (receipt or
    consumption). quantity is always an unsigned magnitude - transaction_type
    alone determines the sign in any aggregate, so there's never two
    disagreeing sources of truth for direction (same convention as
    PurchaseOrderReceiptLine.quantity, which is also always positive).
    """

    __tablename__ = "lot_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # receipt | consumption | transfer_out | transfer_in
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # e.g. "production" | "scrap" | "shipment" | "adjustment" - consumption only.
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Set only for transaction_type="receipt" - which specific PO receiving
    # event contributed to this lot. Nullable rather than a landmine: a
    # future production-order genealogy feature would add its own nullable
    # source_production_order_line_id alongside this, not replace it.
    source_receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_order_receipt_lines.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
