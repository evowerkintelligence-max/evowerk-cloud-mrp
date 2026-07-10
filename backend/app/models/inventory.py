from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryBalance(Base):
    """Running on-hand quantity for a NON-lot-tracked item. Lot-tracked items
    deliberately have no row here - their on-hand is the sum of their active
    Lot.quantity_remaining, and a second parallel source of truth would only let
    the two disagree. The one-active-source rule is enforced in the routes:
    consume/adjust reject lot-tracked items, and PO receiving only writes here
    for non-lot-tracked ones.

    quantity_on_hand is the source of truth for validation/planning;
    InventoryTransaction below is the durable ledger a running total alone can't
    provide (who moved how much, when, why). Same split as Lot vs LotTransaction
    and PurchaseOrderLine.quantity_received vs PurchaseOrderReceiptLine.
    """

    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("org_id", "item_id", "location_id", name="uq_inventory_balances_org_item_loc"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True)
    # On-hand is per location: one balance row per (org, item, location). Total
    # on-hand for an item is the sum across its locations (see MRP netting).
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class InventoryTransaction(Base):
    """Append-only ledger of every on-hand change for a non-lot-tracked item.

    quantity_delta is SIGNED (a receipt is +, a consumption is -, an adjustment
    is either) - deliberately unlike LotTransaction.quantity, which is an
    unsigned magnitude whose direction comes from transaction_type. A general
    inventory ledger has bidirectional adjustments (a cycle-count correction can
    go up or down), so a single signed column keeps the invariant
    "sum(quantity_delta) == quantity_on_hand" trivially checkable, with no
    per-type sign table to keep in sync.
    """

    __tablename__ = "inventory_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True)
    # Which location this movement hit. A transfer records two rows: a
    # transfer_out at the source and a transfer_in at the destination.
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # receipt | consumption | adjustment | transfer_out | transfer_in
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # e.g. "production" | "scrap" | "shipment" | "cycle count" - free text.
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Set only for transaction_type="receipt": which PO receiving event fed this
    # movement. Nullable - consumptions and adjustments have no source receipt.
    source_receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_order_receipt_lines.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
