from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_suppliers_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Deactivated suppliers stay on past POs (FK is ON DELETE CASCADE, not
    # used for this) but drop out of "pick a supplier" pickers - see
    # list_suppliers' active_only filter in app.api.routes.suppliers.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Expected days from PO submit to delivery - used to default a new PO's
    # expected_delivery_date when the caller doesn't supply one. See
    # app.api.routes.purchase_orders.create_purchase_order and
    # app.api.routes.forecasts._new_draft_po.
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Freeform, user-typed - same JSON-list convention as Kpi.tags
    # (app.models.kpi). No dedicated array type: SQLite (used by the
    # in-memory test suite) can't compile a native array column.
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SupplierContact(Base):
    __tablename__ = "supplier_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Not DB-enforced as unique-per-supplier (a partial unique index would
    # need its own transaction to swap primaries atomically). The API
    # enforces "at most one primary" by unsetting any existing primary in
    # the same transaction when a new one is marked - see
    # app.api.routes.suppliers._unset_existing_primary.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("org_id", "po_number", name="uq_pos_org_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Server-computed (max po_number for this org + 1) at creation time, never
    # user-supplied - see app.api.routes.purchase_orders.create_purchase_order.
    # Plain int, not a padded string like "PO-0004" - format that at the
    # response/frontend layer if wanted, never store the formatted string.
    po_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # draft | submitted | partially_received | received | cancelled. Status is
    # the single source of truth for whether receiving has started - it flips
    # to partially_received the moment any line gets a receipt, which is also
    # what gates whether cancel is still allowed. See the receive/cancel routes.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    po_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Running accumulator, kept in sync by the receive route. Fine for status
    # checks/validation; PurchaseOrderReceiptLine below is the durable history
    # of individual receiving events, which this total alone can't reconstruct.
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    # Negotiated net price, tax-exclusive - documented convention now since
    # retrofitting tax/discount semantics onto historical rows later isn't
    # reconstructable (same reasoning as BomLine's unit-of-measure comment).
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PurchaseOrderReceiptLine(Base):
    """Append-only: one row per receiving event against a PurchaseOrderLine.
    Not the source of truth for "how much has been received so far" (that's
    PurchaseOrderLine.quantity_received, kept in sync alongside this) - this
    table exists so "who received how much, when, in which shipment" is
    still answerable later, which a running total alone can't provide -
    lot tracking (next on the roadmap) will need this granularity to assign
    lots to specific receipts.
    """

    __tablename__ = "purchase_order_receipt_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    po_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_order_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    received_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Portion of `quantity` that failed inspection - not additional to it.
    # Only quantity - quantity_rejected is applied to inventory/lot stock
    # (see receive_purchase_order); the full `quantity` still counts toward
    # PurchaseOrderLine.quantity_received, closing out the ordered amount -
    # a rejected unit still closes the line, a replacement is a new PO.
    quantity_rejected: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
