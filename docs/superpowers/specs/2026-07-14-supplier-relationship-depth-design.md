# Supplier Relationship Depth

Status: Approved for planning
Date: 2026-07-14

## Problem

`Supplier` (`backend/app/models/procurement.py`) currently holds only
`name`, `email`, `phone`, and `is_active`. That's enough to pick a supplier
on a purchase order, but not enough to actually manage the relationship:
there's no way to record who to contact, where the supplier is located, what
payment/lead-time terms apply, how to categorize suppliers, or how a
supplier is actually performing (on-time delivery, defect rate). This is the
first of several planned expansions to the procurement feature (see
`docs/superpowers/specs/` for future PO-workflow and price-history specs);
it's scoped first because the other planned pieces build on this data.

Two related gaps on the purchasing/receiving side feed the new scorecard and
are included here rather than deferred, since the scorecard is pointless
without them:

- `PurchaseOrder.expected_delivery_date` exists but nothing defaults it, so
  it's usually left blank and on-time tracking has no baseline to compare
  against for most POs.
- Receiving (`receive_purchase_order`) has no concept of rejected/defective
  quantity - everything received is assumed good and goes straight into
  inventory.

## Scope

In scope: supplier contacts, structured address, payment terms, lead time
(and its effect on PO date defaulting), freeform tags, and a computed
on-time/defect scorecard, including the minimal reject-quantity tracking on
receiving that the scorecard needs.

Out of scope (deferred to future specs per the earlier decomposition): PO
approval workflow, blanket/recurring POs, RFQ/quote comparison, price
history, multi-currency, invoicing/three-way match, emailing POs to
suppliers, and any supplier-facing self-service portal.

## Data model

New table `supplier_contacts`:

```
supplier_contacts
  id           uuid pk
  org_id       uuid fk orgs.id, cascade delete
  supplier_id  uuid fk suppliers.id, cascade delete
  name         varchar(255) not null
  role         varchar(100) nullable   -- freeform, e.g. "Sales Rep", "AP"
  email        varchar(320) nullable
  phone        varchar(50) nullable
  is_primary   boolean not null default false
```

Only one contact per supplier should be primary in practice, but this is not
DB-enforced (a single partial unique index would need a transaction to swap
primaries atomically, which isn't worth the complexity here) - the API
enforces it by unsetting any existing primary when a new one is marked, in
the same transaction as the write.

`Supplier` gains:

```
address_line1      varchar(255) nullable
address_line2      varchar(255) nullable
city                varchar(100) nullable
state               varchar(100) nullable
postal_code         varchar(20)  nullable
country             varchar(100) nullable
payment_terms_days  integer nullable   -- e.g. 30 for "Net 30"
lead_time_days      integer nullable   -- expected days from PO submit to delivery
tags                text[] not null default '{}'
```

`PurchaseOrderReceiptLine` gains:

```
quantity_rejected  numeric(12,4) not null default 0
reject_reason       varchar(500) nullable
```

`quantity_rejected` is part of (not additional to) the receipt's `quantity`
- i.e. `quantity` is the total physically received, `quantity_rejected` is
how much of that was bad. Only `quantity - quantity_rejected` is applied to
inventory/lot stock (`apply_inventory_delta`, `LotTransaction`).
`PurchaseOrderLine.quantity_received` keeps accumulating the full `quantity`
including rejects, per the existing "quantity_received caps at
quantity_ordered" invariant - a rejected unit still closes out the ordered
quantity; getting a replacement means a new PO. This keeps the row-locked
receiving math in `receive_purchase_order` unchanged except for the extra
field.

One migration adds all of the above (new table + new columns on two existing
tables).

## Backend API

`backend/app/api/routes/suppliers.py`:

- `SupplierCreateRequest` / `SupplierUpdateRequest` / `SupplierResponse` gain
  the new address/terms/tags fields (all optional on create, `tags` defaults
  to `[]`).
- `GET /suppliers` gains `?tag=` (repeatable or comma-separated - match the
  existing `q` param's simplicity, single value is enough for v1) filtering
  suppliers whose `tags` array contains it.
- `POST /suppliers/{id}/contacts`, `PATCH /suppliers/{id}/contacts/{contact_id}`,
  `DELETE /suppliers/{id}/contacts/{contact_id}` - owner-only, same pattern
  as the existing supplier mutations (404 via `get_or_404`, audit log entry).
  Setting `is_primary: true` unsets the previous primary contact for that
  supplier in the same transaction.
- `GET /suppliers/{id}/contacts` - list, any authenticated role.
- `GET /suppliers/{id}/scorecard` - any authenticated role. Returns:
  ```
  { on_time_pct: float | null, defect_pct: float | null, sample_size: int }
  ```
  - `on_time_pct`: among this supplier's POs with `status="received"` and a
    non-null `expected_delivery_date`, the fraction where the latest
    `PurchaseOrderReceiptLine.received_at` across all its lines is `<=`
    `expected_delivery_date`. `null` if `sample_size` (the denominator) is 0.
  - `defect_pct`: `sum(quantity_rejected) / sum(quantity)` across all receipt
    lines for this supplier's POs. `null` if nothing has been received yet.
  - Computed on read directly from existing tables - no new stats table or
    scheduled job. This app has no cron/scheduled-job infrastructure today,
    and supplier counts are small enough that an on-demand aggregate query
    is cheap; revisit only if a supplier list page ever needs this at scale
    (it currently won't - the scorecard is fetched per-supplier on the
    detail view, not in the list).

`backend/app/api/routes/purchase_orders.py`:

- `ReceiveLineInput` gains optional `quantity_rejected: Decimal = 0` and
  `reject_reason: str | None`. Validation in the existing pre-write loop
  extends: `quantity_rejected` must be `>= 0` and `<= quantity`.
  `apply_inventory_delta`/lot-transaction calls use
  `quantity - quantity_rejected` as the delta instead of `quantity`; if that
  nets to 0 (fully rejected), skip the inventory/lot call entirely (no
  zero-quantity transactions).
- `create_purchase_order`: if the request doesn't supply
  `expected_delivery_date`, default it to
  `today + supplier.lead_time_days` when the supplier has a
  `lead_time_days` set, else leave it null (unchanged behavior).

## Frontend

`Suppliers.tsx`:
- Edit dialog grows: address fields, payment terms / lead time number
  inputs, a tag input (add/remove chips) - all alongside the existing
  name/email/phone fields.
- A contacts sub-section inside the edit dialog: list of contacts with
  inline add/edit/remove, a "make primary" action per row.
- Supplier list gains tag-filter chips above the table (derived from the
  distinct tags present across the org's loaded suppliers) and continues to
  combine with the existing search/pagination.
- Supplier detail (or an expandable row/section) shows the scorecard:
  on-time % and defect % with the existing sample size, or "Not enough data
  yet" when `sample_size` is 0.

`PurchaseOrders.tsx` receive dialog: each line gains a rejected-quantity
input (defaults 0, capped client-side at the accepted quantity) and an
optional reason text input, next to the existing quantity-to-receive input.

`GeneratePos.tsx` / PO creation: expected delivery date input pre-fills from
the selected supplier's `lead_time_days` (today + lead time) when the field
is still empty; user can always override it.

## Testing

Backend (extending the existing `test_supplier_lifecycle.py` and
`test_purchase_order_listing.py` patterns):
- Contacts CRUD, including the primary-swap behavior and owner-role
  enforcement.
- Tag filter on `GET /suppliers`.
- Scorecard math: seed POs/receipts with known on-time/late and
  accepted/rejected splits, assert the returned percentages and
  `sample_size`, and assert `null` when there's no data yet.
- Receive endpoint: `quantity_rejected` validation (negative, exceeds
  `quantity`), inventory delta only reflects the accepted portion, a fully
  rejected receipt applies no inventory/lot transaction, and
  `quantity_received`/PO status logic is unchanged (still counts the full
  received quantity).
- `create_purchase_order` defaulting `expected_delivery_date` from
  `lead_time_days`, and leaving it null when the supplier has none set.

No changes needed to the existing PO concurrency tests
(`test_concurrent_po_receive_never_exceeds_ordered_quantity` etc.) - the
row-locking and `quantity_received` accounting they cover are unchanged.
