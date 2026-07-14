# Inventory Adjust Approval + Cycle Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `manager` role with day-to-day inventory access, gate the `adjust`/new `count` endpoints behind an org-configurable quantity threshold with a pending-approval queue for owners, and require a fixed reason code on every adjustment.

**Architecture:** Backend-first. A new `manager` role value (no schema change - `User.role` is already an unconstrained string). A new `inventory_adjustment_requests` table holds over-threshold submissions. A shared internal helper (`_post_or_queue_adjustment`) is the single place that decides "apply now" vs "queue for approval" - both the existing `/adjust` endpoint and the new `/count` endpoint call it, so there is exactly one approval code path to test and trust. Approval replays the stored delta through the existing `apply_inventory_delta` guarded UPDATE, so a stale approval can't corrupt on-hand - no new concurrency code needed there. Frontend wires a Count form (physical count in, variance computed server-side) alongside the existing Adjust form (relabeled for paperwork-only corrections), plus a new "Approvals" tab on the Warehouse page.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend), React + TanStack Query + TypeScript (frontend), pytest + pytest-asyncio (backend tests), no frontend test runner exists in this repo (verify frontend changes via `npm run build` + manual check in the dev server).

**Task grouping:** Tasks are organized **by file, not by endpoint** - every file in this plan is written or modified in exactly one task, never touched again later. This costs some internal TDD granularity within the larger tasks (Task 2 in particular bundles what would otherwise be five separate endpoint slices), but it means a fresh subagent dispatched per task never has to re-read and re-orient on a file another task already changed. Within each task, the step sequence still follows write-tests -> verify-red -> implement -> verify-green -> commit; it's just scoped to "everything this file needs," not "one endpoint."

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-14-inventory-adjust-approval-design.md` - every task below implements one piece of it; the "Data model", "Backend API", "Frontend", and "Testing" sections there are the source of truth if this plan and the spec ever disagree.
- Default `Org.adjust_approval_threshold` is `50` (`Decimal("50")` / migration `server_default="50"`) - exact value from the spec, not a placeholder.
- Reason codes are exactly: `cycle_count | damage | found_stock | shrinkage | correction | other` - this exact list, in this order, everywhere it appears (backend `Literal`, frontend `REASON_CODES`).
- `reason_code == "other"` always requires a non-empty `reason_note`, enforced both in the shared Pydantic validator and (for UX) in the frontend forms.
- Breaking change: `POST /inventory/{item_id}/adjust` currently accepts an optional `reason` string; after Task 2 it requires `reason_code` and returns a different response shape (`AdjustmentOutcome` instead of a flat `InventoryItemResponse`). This is intentional per the spec - do not preserve backward compatibility.
- Every new/changed backend mutation gets an audit log entry via the existing `log_event` helper (`app/middleware/audit.py`), following the existing `inventory.consumed` / `inventory.adjusted` / `inventory.transferred` naming: `inventory.adjustment_requested`, `inventory.adjustment_approved`, `inventory.adjustment_rejected`, `org.threshold_updated`.
- Backend commands run from the `backend/` directory. Frontend commands run from the `frontend/` directory.
- **One file, one task.** If you find yourself about to modify a file a previous task already touched, stop - that's a sign this plan needs updating, not a sign to proceed.

---

### Task 1: Schema foundation - manager-ready columns and the request table

**Files:**
- Create: `backend/alembic/versions/d47b6e19a3c8_inventory_adjustment_approval.py`
- Modify: `backend/app/models/tenant.py`
- Modify: `backend/app/models/inventory.py`
- Modify: `backend/app/services/inventory.py`

**Interfaces:**
- Produces: `Org.adjust_approval_threshold: Decimal` (default `Decimal("50")`); `InventoryTransaction.counted_quantity: Decimal | None`, `InventoryTransaction.expected_quantity: Decimal | None`, `InventoryTransaction.reason_note: str | None`; new model `InventoryAdjustmentRequest` (table `inventory_adjustment_requests`) with fields `id, org_id, item_id, location_id, quantity_delta, counted_quantity, expected_quantity_at_submit, reason_code, reason_note, status, requested_by, requested_at, reviewed_by, reviewed_at, review_note`; `apply_inventory_delta(..., counted_quantity: Decimal | None = None, expected_quantity: Decimal | None = None, reason_note: str | None = None)` - three new optional kwargs, fully backward compatible with every existing caller (`consume_inventory`, `transfer_inventory`, PO receiving).

This is a schema-only, purely additive task - there is no new user-facing behavior yet to write a failing test against. Verification is the migration up/down/up cycle plus a full regression run of the existing suite (must stay green - nothing here changes behavior of any existing endpoint).

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/d47b6e19a3c8_inventory_adjustment_approval.py`:

```python
"""inventory adjustment approval

Revision ID: d47b6e19a3c8
Revises: c8a1e5f93d64
Create Date: 2026-07-15

"""
from alembic import op
import sqlalchemy as sa

revision = "d47b6e19a3c8"  # pragma: allowlist secret
down_revision = "c8a1e5f93d64"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("adjust_approval_threshold", sa.Numeric(12, 4), nullable=False, server_default="50"),
    )

    op.add_column("inventory_transactions", sa.Column("counted_quantity", sa.Numeric(12, 4), nullable=True))
    op.add_column("inventory_transactions", sa.Column("expected_quantity", sa.Numeric(12, 4), nullable=True))
    op.add_column("inventory_transactions", sa.Column("reason_note", sa.String(500), nullable=True))

    op.create_table(
        "inventory_adjustment_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column(
            "location_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity_delta", sa.Numeric(12, 4), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("expected_quantity_at_submit", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason_code", sa.String(20), nullable=False),
        sa.Column("reason_note", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=True),
    )
    op.create_index("ix_inventory_adjustment_requests_org_id", "inventory_adjustment_requests", ["org_id"])
    op.create_index("ix_inventory_adjustment_requests_item_id", "inventory_adjustment_requests", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_adjustment_requests_item_id", table_name="inventory_adjustment_requests")
    op.drop_index("ix_inventory_adjustment_requests_org_id", table_name="inventory_adjustment_requests")
    op.drop_table("inventory_adjustment_requests")

    op.drop_column("inventory_transactions", "reason_note")
    op.drop_column("inventory_transactions", "expected_quantity")
    op.drop_column("inventory_transactions", "counted_quantity")

    op.drop_column("orgs", "adjust_approval_threshold")
```

- [ ] **Step 2: Run the migration up, down, and up again**

Run (from `backend/`): `python -m alembic upgrade head`
Expected: no errors, ends on `d47b6e19a3c8`.

Run: `python -m alembic downgrade -1`
Expected: no errors, back to `c8a1e5f93d64`.

Run: `python -m alembic upgrade head`
Expected: no errors, back on `d47b6e19a3c8`.

- [ ] **Step 3: Add `Org.adjust_approval_threshold`**

In `backend/app/models/tenant.py`, change the imports and add the field:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Manager-submitted adjust/count deltas at or below this magnitude post
    # immediately; over it, they queue for owner approval. See
    # app.api.routes.inventory._post_or_queue_adjustment.
    adjust_approval_threshold: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("50")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Extend `InventoryTransaction` and add `InventoryAdjustmentRequest`**

In `backend/app/models/inventory.py`, add three columns to `InventoryTransaction` right after the existing `reason` field (after line 82, before `source_receipt_line_id`):

```python
    # e.g. "production" | "scrap" | "shipment" | "cycle count" - free text.
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Populated only when transaction_type="adjustment" and the adjustment came
    # from POST /inventory/{item_id}/count - null for every other path
    # (raw /adjust, consume, transfer, receipt).
    counted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    expected_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    # Elaboration for reason="other"; optional for every other reason code.
    reason_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

Then append a new class at the end of the file:

```python
class InventoryAdjustmentRequest(Base):
    """A manager-submitted adjust/count whose |delta| exceeded the org's
    adjust_approval_threshold, parked here until an owner approves or rejects
    it. Approval replays quantity_delta through apply_inventory_delta, so the
    same non-negative guard that protects every other inventory write also
    protects a stale approval (on-hand dropped between submission and review)
    - see apply_inventory_delta's docstring, no separate guard needed here.
    """

    __tablename__ = "inventory_adjustment_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Set only when submitted via POST /inventory/{item_id}/count.
    counted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    # Snapshot of on-hand at submission time, for display only - approval
    # re-checks live on-hand via apply_inventory_delta's guard, not this.
    expected_quantity_at_submit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

No new imports needed - `String`, `Numeric`, `ForeignKey`, `Uuid`, `DateTime`, `Mapped`, `mapped_column`, `Decimal`, `datetime`, `timezone`, `uuid`, `Base` are already imported at the top of `inventory.py`.

- [ ] **Step 5: Extend `apply_inventory_delta`**

In `backend/app/services/inventory.py`, change the signature and the `InventoryTransaction` construction:

```python
async def apply_inventory_delta(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    delta: Decimal,
    transaction_type: str,
    created_by: uuid.UUID,
    reason: str | None = None,
    source_receipt_line_id: uuid.UUID | None = None,
    counted_quantity: Decimal | None = None,
    expected_quantity: Decimal | None = None,
    reason_note: str | None = None,
) -> Decimal:
    """Apply a signed delta to a non-lot-tracked item's on-hand balance at one
    location and record an append-only InventoryTransaction. Returns the new
    on-hand quantity at that location.

    Concurrency-safe by construction, no read-modify-write:
    - The update is a guarded, server-computed `quantity_on_hand = ... + delta`,
      so two concurrent deltas can't both read the same starting value and lose
      one increment (the bug fixed for lot top-up).
    - For a negative delta the same UPDATE carries a `... + delta >= 0` guard, so
      the row can't go negative even under a concurrent consume; a zero-row
      result means the guard bit and we raise InsufficientInventory.
    - The first movement for an item creates the balance row inside a SAVEPOINT
      and retries as an update on IntegrityError, so a concurrent first-create
      can't 500 and can't discard the caller's other pending writes (this runs
      inside receive_purchase_order's multi-line loop).
    """
    new_on_hand = await _bump_balance(
        db, org_id=org_id, item_id=item_id, location_id=location_id, delta=delta
    )

    db.add(
        InventoryTransaction(
            org_id=org_id,
            item_id=item_id,
            location_id=location_id,
            transaction_type=transaction_type,
            quantity_delta=delta,
            reason=reason,
            reason_note=reason_note,
            counted_quantity=counted_quantity,
            expected_quantity=expected_quantity,
            source_receipt_line_id=source_receipt_line_id,
            created_by=created_by,
        )
    )
    await db.flush()
    return new_on_hand
```

Only the signature and the `InventoryTransaction(...)` call change - `_bump_balance` is untouched.

- [ ] **Step 6: Run the full existing suite to confirm no regressions**

Run: `pytest -q`
Expected: PASS, same result as before this task - these changes are purely additive (new optional kwargs, new nullable columns, new table), so no existing test's behavior changes.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/d47b6e19a3c8_inventory_adjustment_approval.py backend/app/models/tenant.py backend/app/models/inventory.py backend/app/services/inventory.py
git commit -m "feat: add schema for inventory adjustment approval requests"
```

---

### Task 2: Inventory routes - manager role, reason codes, threshold gate, count, approval queue

The largest task in this plan, by design: `backend/app/api/routes/inventory.py` and its two test files are fully finished here, in one pass, so no later task ever touches them again. Everything from "manager can consume/transfer" through "owner can approve a pending request" lands together.

**Files:**
- Modify: `backend/app/api/routes/inventory.py`
- Modify: `backend/tests/test_inventory.py`
- Create: `backend/tests/test_inventory_adjustment_requests.py`

**Interfaces:**
- Consumes: `apply_inventory_delta(..., counted_quantity=, expected_quantity=, reason_note=)`, `InventoryAdjustmentRequest`, `Org.adjust_approval_threshold` from Task 1.
- Produces (used by Task 3 and Task 4, both of which only *read* these - they don't edit this file): `ReasonCode` (`Literal["cycle_count", "damage", "found_stock", "shrinkage", "correction", "other"]`); `AdjustmentRequestResponse`; `AdjustmentOutcome` (`{applied: bool, item: InventoryItemResponse | None, request: AdjustmentRequestResponse | None}`); `POST /inventory/{item_id}/adjust` (rewritten), `POST /inventory/{item_id}/count` (new), `GET /inventory/adjustment-requests`, `POST /inventory/adjustment-requests/{id}/approve`, `POST /inventory/adjustment-requests/{id}/reject`; test helpers `_add_user_with_role(test_engine, org_id, email, role)` and `_login(client, email)` in `test_inventory.py`, imported by `test_inventory_adjustment_requests.py` and by Task 3/4's test files via `from tests.test_inventory import ...` (the cross-module import pattern already used by `tests/test_rate_limit.py` importing from `tests/test_auth.py`).

- [ ] **Step 1: Write every failing test this task needs**

First, in `backend/tests/test_inventory.py`, add these two helpers after the existing helper functions (after `_on_hand`, before `test_receiving_non_lot_item_accrues_on_hand`):

```python
async def _add_user_with_role(test_engine, org_id: str, email: str, role: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import uuid as uuid_module

    from app.core.security import hash_password
    from app.models.user import User

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email=email,
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=True,
            )
        )
        await session.commit()


async def _login(client, email: str) -> dict[str, str]:
    res = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
```

Add the manager consume/transfer test:

```python
async def test_manager_can_consume_and_transfer(client, test_engine):
    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    other_loc = await client.post(
        "/locations", json={"code": "WH2", "name": "Warehouse 2"}, headers=headers
    )
    other_location_id = other_loc.json()["id"]
    default_location_id = next(
        l["id"] for l in (await client.get("/locations", headers=headers)).json() if l["is_default"]
    )

    await _add_user_with_role(test_engine, org_id, "manager1@example.com", "manager")
    manager_headers = await _login(client, "manager1@example.com")

    consume = await client.post(
        f"/inventory/{item['id']}/consume", json={"quantity": "10"}, headers=manager_headers
    )
    assert consume.status_code == 200
    assert _dec(consume.json()["quantity_on_hand"]) == Decimal("40")

    transfer = await client.post(
        f"/inventory/{item['id']}/transfer",
        json={
            "from_location_id": default_location_id,
            "to_location_id": other_location_id,
            "quantity": "5",
        },
        headers=manager_headers,
    )
    assert transfer.status_code == 200
    balances = {b["location_id"]: _dec(b["quantity_on_hand"]) for b in transfer.json()["by_location"]}
    assert balances[other_location_id] == Decimal("5")
    assert balances[default_location_id] == Decimal("30")
```

Now replace `test_adjust_up_and_down`, `test_adjust_zero_rejected`, and `test_consume_and_adjust_reject_lot_tracked_item` with the `reason_code`-aware versions, and add the new reason-code/threshold/count tests:

```python
async def test_adjust_up_and_down(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    up = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "5", "reason_code": "correction"},
        headers=headers,
    )
    assert up.status_code == 200
    up_body = up.json()
    assert up_body["applied"] is True
    assert _dec(up_body["item"]["quantity_on_hand"]) == Decimal("15")

    down = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-3", "reason_code": "shrinkage"},
        headers=headers,
    )
    assert down.status_code == 200
    assert _dec(down.json()["item"]["quantity_on_hand"]) == Decimal("12")

    # An adjustment below zero is rejected, on-hand unchanged.
    too_low = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-100", "reason_code": "correction"},
        headers=headers,
    )
    assert too_low.status_code == 409
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("12")


async def test_adjust_zero_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "0", "reason_code": "correction"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_adjust_requires_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust", json={"quantity": "1"}, headers=headers
    )
    assert res.status_code == 422


async def test_adjust_rejects_unknown_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "made_up"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_adjust_other_reason_requires_note(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    missing_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other"},
        headers=headers,
    )
    assert missing_note.status_code == 422

    with_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other", "reason_note": "warehouse fire, see incident report"},
        headers=headers,
    )
    assert with_note.status_code == 200


async def test_consume_and_adjust_reject_lot_tracked_item(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    assert (
        await client.post(f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=headers,
        )
    ).status_code == 400


async def test_manager_adjust_within_threshold_applies_immediately(client, test_engine):
    token = await _register_and_login(client, email="owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager4@example.com", "manager")
    manager_headers = await _login(client, "manager4@example.com")

    # default threshold is 50; a delta of 10 is within it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "10", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("110")


async def test_manager_adjust_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager5@example.com", "manager")
    manager_headers = await _login(client, "manager5@example.com")

    # default threshold is 50; a delta of 60 exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert body["request"]["status"] == "pending"
    assert _dec(body["request"]["quantity_delta"]) == Decimal("60")
    assert _dec(body["request"]["expected_quantity_at_submit"]) == Decimal("100")

    # on-hand is unchanged - nothing was posted
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("100")


async def test_owner_adjust_over_threshold_still_applies_immediately(client):
    token = await _register_and_login(client, email="owner6@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("160")


async def test_count_computes_variance_and_applies_within_threshold(client):
    token = await _register_and_login(client, email="owner7@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "42"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("42")

    detail = await _on_hand(client, token, item)
    last_tx = detail["transactions"][-1]
    assert last_tx["transaction_type"] == "adjustment"
    assert _dec(last_tx["quantity_delta"]) == Decimal("-8")
    assert _dec(last_tx["counted_quantity"]) == Decimal("42")
    assert _dec(last_tx["expected_quantity"]) == Decimal("50")
    assert last_tx["reason"] == "cycle_count"


async def test_count_no_variance_rejected(client):
    token = await _register_and_login(client, email="owner8@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "50"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_count_against_no_balance_treats_expected_as_zero(client):
    token = await _register_and_login(client, email="owner9@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)  # never received - no balance row

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "5"},
        headers=headers,
    )
    assert res.status_code == 200
    assert _dec(res.json()["item"]["quantity_on_hand"]) == Decimal("5")


async def test_manager_count_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner10@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager10@example.com", "manager")
    manager_headers = await _login(client, "manager10@example.com")

    # default threshold is 50; counting 20 (variance -80) exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "20"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert _dec(body["request"]["quantity_delta"]) == Decimal("-80")
    assert _dec(body["request"]["counted_quantity"]) == Decimal("20")
    assert body["request"]["reason_code"] == "cycle_count"
```

In `test_rbac_blocks_non_owner_from_inventory_mutations`, update the adjust assertion:

```python
    assert (
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=member_headers,
        )
    ).status_code == 403
```

Finally, create `backend/tests/test_inventory_adjustment_requests.py` in full - list, approve, and reject tests together, since this file is created exactly once:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_inventory import (
    PASSWORD,
    _add_user_with_role,
    _create_item,
    _create_supplier,
    _dec,
    _login,
    _receive,
    _register_and_login,
)

pytestmark = pytest.mark.asyncio


async def _submit_over_threshold_request(client, test_engine, owner_email, manager_email):
    token = await _register_and_login(client, email=owner_email)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, manager_email, "manager")
    manager_headers = await _login(client, manager_email)

    submit = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert submit.status_code == 200
    request_id = submit.json()["request"]["id"]
    return headers, manager_headers, item, request_id


async def test_owner_and_manager_can_list_pending_requests(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner11@example.com", "manager11@example.com"
    )

    owner_view = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    assert owner_view.status_code == 200
    assert any(r["id"] == request_id for r in owner_view.json())

    manager_view = await client.get(
        "/inventory/adjustment-requests?status=pending", headers=manager_headers
    )
    assert manager_view.status_code == 200
    assert any(r["id"] == request_id for r in manager_view.json())


async def test_member_cannot_list_pending_requests(client, test_engine):
    token = await _register_and_login(client, email="owner12@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    await _add_user_with_role(test_engine, org_id, "member12@example.com", "member")
    member_headers = await _login(client, "member12@example.com")

    res = await client.get("/inventory/adjustment-requests?status=pending", headers=member_headers)
    assert res.status_code == 403


async def test_default_status_filter_is_pending(client, test_engine):
    headers, _, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner13@example.com", "manager13@example.com"
    )

    res = await client.get("/inventory/adjustment-requests", headers=headers)
    assert res.status_code == 200
    assert all(r["status"] == "pending" for r in res.json())
    assert any(r["id"] == request_id for r in res.json())


async def test_owner_can_approve_pending_request(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner14@example.com", "manager14@example.com"
    )
    # requested_by should end up as the transaction's created_by after approval -
    # the ledger attributes the movement to whoever asked for it, not the approver.
    pending = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    requested_by = next(r for r in pending.json() if r["id"] == request_id)["requested_by"]

    approve = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve",
        json={"review_note": "checked the shelf myself"},
        headers=headers,
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body["status"] == "approved"
    assert body["review_note"] == "checked the shelf myself"

    detail = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert _dec(detail.json()["quantity_on_hand"]) == Decimal("160")
    last_tx = detail.json()["transactions"][-1]
    assert last_tx["transaction_type"] == "adjustment"
    assert _dec(last_tx["quantity_delta"]) == Decimal("60")
    assert last_tx["created_by"] == requested_by


async def test_manager_cannot_approve(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner15@example.com", "manager15@example.com"
    )

    res = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=manager_headers
    )
    assert res.status_code == 403


async def test_approving_already_reviewed_request_conflicts(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner16@example.com", "manager16@example.com"
    )

    first = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=headers
    )
    assert second.status_code == 409


async def test_owner_can_reject_pending_request_with_no_balance_impact(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner17@example.com", "manager17@example.com"
    )

    reject = await client.post(
        f"/inventory/adjustment-requests/{request_id}/reject",
        json={"review_note": "count looks wrong, recount needed"},
        headers=headers,
    )
    assert reject.status_code == 200
    body = reject.json()
    assert body["status"] == "rejected"
    assert body["review_note"] == "count looks wrong, recount needed"

    # on-hand is unchanged - the +60 never posted
    detail = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert _dec(detail.json()["quantity_on_hand"]) == Decimal("100")

    # no longer shows up in the pending queue
    pending = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    assert not any(r["id"] == request_id for r in pending.json())


async def test_manager_cannot_reject(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner18@example.com", "manager18@example.com"
    )

    res = await client.post(
        f"/inventory/adjustment-requests/{request_id}/reject", json={}, headers=manager_headers
    )
    assert res.status_code == 403
```

- [ ] **Step 2: Run everything to verify it fails**

Run: `pytest tests/test_inventory.py tests/test_inventory_adjustment_requests.py -v`
Expected: FAIL - `AdjustRequest` doesn't accept `reason_code` yet, `/count` and `/inventory/adjustment-requests*` routes don't exist (404s), manager gets 403 on consume/transfer.

- [ ] **Step 3: Implement the full route file**

In `backend/app/api/routes/inventory.py`, update the imports at the top:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.inventory import InventoryAdjustmentRequest, InventoryBalance, InventoryTransaction
from app.models.location import Location
from app.models.tenant import Org
from app.services.inventory import apply_inventory_delta
from app.services.locations import resolve_location_id

router = APIRouter(prefix="/inventory", tags=["inventory"])

ReasonCode = Literal["cycle_count", "damage", "found_stock", "shrinkage", "correction", "other"]
```

Replace the `AdjustRequest` class with `_ReasonFields` + `AdjustRequest` + `CountRequest`:

```python
class _ReasonFields(BaseModel):
    reason_code: ReasonCode
    reason_note: str | None = None

    @model_validator(mode="after")
    def _note_required_for_other(self) -> "_ReasonFields":
        if self.reason_code == "other" and not self.reason_note:
            raise ValueError("reason_note is required when reason_code is 'other'")
        return self


class AdjustRequest(_ReasonFields):
    # Signed correction: +3 found more on the shelf, -3 found fewer. A cycle
    # count is entered as the delta from the system figure, not the new
    # absolute - that keeps the write a single guarded increment with no
    # read-modify-write race (see services.inventory.apply_inventory_delta).
    quantity: Decimal
    location_id: str | None = None


class CountRequest(_ReasonFields):
    location_id: str | None = None
    counted_quantity: Decimal
    reason_code: ReasonCode = "cycle_count"


class ApproveRejectRequest(BaseModel):
    review_note: str | None = None
```

Add the three new fields to `InventoryTransactionResponse`:

```python
class InventoryTransactionResponse(BaseModel):
    id: str
    location_id: str
    location_code: str
    transaction_type: str
    quantity_delta: Decimal
    reason: str | None
    counted_quantity: Decimal | None
    expected_quantity: Decimal | None
    reason_note: str | None
    created_by: str
    created_at: datetime
```

Add the response models right after `InventoryDetailResponse`:

```python
class AdjustmentRequestResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    location_id: str
    location_code: str
    quantity_delta: Decimal
    counted_quantity: Decimal | None
    expected_quantity_at_submit: Decimal
    reason_code: str
    reason_note: str | None
    status: str
    requested_by: str
    requested_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None


class AdjustmentOutcome(BaseModel):
    applied: bool
    item: InventoryItemResponse | None = None
    request: AdjustmentRequestResponse | None = None
```

Update `get_inventory`'s construction of `InventoryTransactionResponse` (find the existing `InventoryTransactionResponse(` call inside `get_inventory`):

```python
    transactions = [
        InventoryTransactionResponse(
            id=str(tx.id),
            location_id=str(tx.location_id),
            location_code=loc.code,
            transaction_type=tx.transaction_type,
            quantity_delta=tx.quantity_delta,
            reason=tx.reason,
            counted_quantity=tx.counted_quantity,
            expected_quantity=tx.expected_quantity,
            reason_note=tx.reason_note,
            created_by=str(tx.created_by),
            created_at=tx.created_at,
        )
        for tx, loc in tx_result.all()
    ]
```

Add the shared helpers right after `_item_response` (before `list_inventory`):

```python
async def _current_on_hand(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, location_id: uuid.UUID
) -> Decimal:
    return (
        await db.execute(
            select(InventoryBalance.quantity_on_hand).where(
                InventoryBalance.org_id == org_id,
                InventoryBalance.item_id == item_id,
                InventoryBalance.location_id == location_id,
            )
        )
    ).scalar_one_or_none() or Decimal("0.0000")


def _adjustment_request_response(
    req: InventoryAdjustmentRequest, item: Item, location: Location
) -> AdjustmentRequestResponse:
    return AdjustmentRequestResponse(
        id=str(req.id),
        item_id=str(item.id),
        item_sku=item.sku,
        location_id=str(location.id),
        location_code=location.code,
        quantity_delta=req.quantity_delta,
        counted_quantity=req.counted_quantity,
        expected_quantity_at_submit=req.expected_quantity_at_submit,
        reason_code=req.reason_code,
        reason_note=req.reason_note,
        status=req.status,
        requested_by=str(req.requested_by),
        requested_at=req.requested_at,
        reviewed_by=str(req.reviewed_by) if req.reviewed_by else None,
        reviewed_at=req.reviewed_at,
        review_note=req.review_note,
    )


async def _post_or_queue_adjustment(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    role: str,
    item: Item,
    location_id: uuid.UUID,
    delta: Decimal,
    current_on_hand: Decimal,
    reason_code: str,
    reason_note: str | None,
    counted_quantity: Decimal | None,
    created_by: uuid.UUID,
) -> AdjustmentOutcome:
    """The single place that decides "apply now" vs "queue for approval",
    shared by /adjust and /count. Owner never queues (owner is the approver).
    Manager queues only when |delta| exceeds the org's threshold."""
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one()
    within_threshold = abs(delta) <= org.adjust_approval_threshold

    if role == "owner" or within_threshold:
        await apply_inventory_delta(
            db,
            org_id=org_id,
            item_id=item.id,
            location_id=location_id,
            delta=delta,
            transaction_type="adjustment",
            created_by=created_by,
            reason=reason_code,
            reason_note=reason_note,
            counted_quantity=counted_quantity,
            expected_quantity=current_on_hand if counted_quantity is not None else None,
        )
        return AdjustmentOutcome(
            applied=True, item=_item_response(item, await _by_location(db, org_id, item.id))
        )

    request = InventoryAdjustmentRequest(
        org_id=org_id,
        item_id=item.id,
        location_id=location_id,
        quantity_delta=delta,
        counted_quantity=counted_quantity,
        expected_quantity_at_submit=current_on_hand,
        reason_code=reason_code,
        reason_note=reason_note,
        requested_by=created_by,
    )
    db.add(request)
    await db.flush()
    location = await get_or_404(db, Location, org_id, location_id, "Location not found")
    return AdjustmentOutcome(applied=False, request=_adjustment_request_response(request, item, location))
```

Change the role dependency on `consume_inventory` and `transfer_inventory`:

```python
@router.post("/{item_id}/consume", response_model=InventoryItemResponse)
async def consume_inventory(
    item_id: uuid.UUID,
    body: ConsumeRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
```

```python
@router.post("/{item_id}/transfer", response_model=InventoryItemResponse)
async def transfer_inventory(
    item_id: uuid.UUID,
    body: TransferRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
```

Replace the whole `adjust_inventory` function:

```python
@router.post("/{item_id}/adjust", response_model=AdjustmentOutcome)
async def adjust_inventory(
    item_id: uuid.UUID,
    body: AdjustRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be a non-zero correction")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    current_on_hand = await _current_on_hand(db, current.org_id, item_id, location_id)

    outcome = await _post_or_queue_adjustment(
        db,
        org_id=current.org_id,
        role=current.role,
        item=item,
        location_id=location_id,
        delta=body.quantity,
        current_on_hand=current_on_hand,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
        counted_quantity=None,
        created_by=current.id,
    )
    await log_event(
        db,
        action="inventory.adjusted" if outcome.applied else "inventory.adjustment_requested",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "delta": str(body.quantity),
            "location_id": str(location_id),
            "reason_code": body.reason_code,
        },
    )
    await db.commit()
    return outcome
```

Add the count endpoint right after `adjust_inventory`:

```python
@router.post("/{item_id}/count", response_model=AdjustmentOutcome)
async def count_inventory(
    item_id: uuid.UUID,
    body: CountRequest,
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    _reject_lot_tracked(item)
    location_id = await resolve_location_id(db, current.org_id, body.location_id)
    current_on_hand = await _current_on_hand(db, current.org_id, item_id, location_id)
    delta = body.counted_quantity - current_on_hand
    if delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no variance - nothing to post")

    outcome = await _post_or_queue_adjustment(
        db,
        org_id=current.org_id,
        role=current.role,
        item=item,
        location_id=location_id,
        delta=delta,
        current_on_hand=current_on_hand,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
        counted_quantity=body.counted_quantity,
        created_by=current.id,
    )
    await log_event(
        db,
        action="inventory.adjusted" if outcome.applied else "inventory.adjustment_requested",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(item_id),
        metadata={
            "delta": str(delta),
            "counted_quantity": str(body.counted_quantity),
            "location_id": str(location_id),
            "reason_code": body.reason_code,
        },
    )
    await db.commit()
    return outcome
```

Add the list/approve/reject endpoints at the end of the file:

```python
@router.get("/adjustment-requests", response_model=list[AdjustmentRequestResponse])
async def list_adjustment_requests(
    status_filter: str | None = Query(default="pending", alias="status"),
    current: CurrentUser = Depends(require_role("owner", "manager")),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(InventoryAdjustmentRequest, Item, Location)
        .join(Item, Item.id == InventoryAdjustmentRequest.item_id)
        .join(Location, Location.id == InventoryAdjustmentRequest.location_id)
        .where(InventoryAdjustmentRequest.org_id == current.org_id)
        .order_by(InventoryAdjustmentRequest.requested_at.desc())
    )
    if status_filter:
        query = query.where(InventoryAdjustmentRequest.status == status_filter)
    rows = await db.execute(query)
    return [_adjustment_request_response(req, item, loc) for req, item, loc in rows.all()]


@router.post(
    "/adjustment-requests/{request_id}/approve", response_model=AdjustmentRequestResponse
)
async def approve_adjustment_request(
    request_id: uuid.UUID,
    body: ApproveRejectRequest = ApproveRejectRequest(),
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    req = await get_or_404(
        db, InventoryAdjustmentRequest, current.org_id, request_id, "Adjustment request not found"
    )
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request already {req.status}")

    item = await get_or_404(db, Item, current.org_id, req.item_id, "Item not found")
    await apply_inventory_delta(
        db,
        org_id=current.org_id,
        item_id=req.item_id,
        location_id=req.location_id,
        delta=req.quantity_delta,
        transaction_type="adjustment",
        created_by=req.requested_by,
        reason=req.reason_code,
        reason_note=req.reason_note,
        counted_quantity=req.counted_quantity,
        expected_quantity=req.expected_quantity_at_submit if req.counted_quantity is not None else None,
    )
    req.status = "approved"
    req.reviewed_by = current.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = body.review_note
    await log_event(
        db,
        action="inventory.adjustment_approved",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(req.id),
        metadata={"item_id": str(req.item_id), "delta": str(req.quantity_delta)},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, req.location_id, "Location not found")
    return _adjustment_request_response(req, item, location)


@router.post(
    "/adjustment-requests/{request_id}/reject", response_model=AdjustmentRequestResponse
)
async def reject_adjustment_request(
    request_id: uuid.UUID,
    body: ApproveRejectRequest = ApproveRejectRequest(),
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    req = await get_or_404(
        db, InventoryAdjustmentRequest, current.org_id, request_id, "Adjustment request not found"
    )
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request already {req.status}")

    item = await get_or_404(db, Item, current.org_id, req.item_id, "Item not found")
    req.status = "rejected"
    req.reviewed_by = current.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = body.review_note
    await log_event(
        db,
        action="inventory.adjustment_rejected",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(req.id),
        metadata={"item_id": str(req.item_id), "review_note": body.review_note},
    )
    await db.commit()
    location = await get_or_404(db, Location, current.org_id, req.location_id, "Location not found")
    return _adjustment_request_response(req, item, location)
```

If `apply_inventory_delta` raises `InsufficientInventory` (409) inside `approve_adjustment_request` - e.g. on-hand dropped below what's needed since the request was submitted - it raises before `req.status` is ever set, and nothing is committed, so the session's rollback-on-close (the same behavior every other multi-step write in this file already relies on) leaves the request untouched at `status="pending"`. No extra try/except needed.

- [ ] **Step 4: Run everything to verify it passes**

Run: `pytest tests/test_inventory.py tests/test_inventory_adjustment_requests.py -v`
Expected: PASS - every test in both files.

- [ ] **Step 5: Run the full backend suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/inventory.py backend/tests/test_inventory.py backend/tests/test_inventory_adjustment_requests.py
git commit -m "feat: manager role, reason codes, threshold-gated adjust/count, and the approval queue"
```

---

### Task 3: Org approval-threshold setting

**Files:**
- Modify: `backend/app/api/routes/orgs.py`
- Create: `backend/tests/test_org_settings.py`

**Interfaces:**
- Consumes: `Org.adjust_approval_threshold` from Task 1; calls `POST /inventory/{item_id}/adjust` (Task 2, read-only usage in a test, not an edit) to prove a changed threshold takes effect.
- Produces: `GET /orgs/me` response gains `adjust_approval_threshold`; new `PATCH /orgs/me`.

A dedicated test file (rather than adding to `test_inventory_adjustment_requests.py`) keeps Task 2's files untouched by this task.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_org_settings.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_inventory import (
    _add_user_with_role,
    _create_item,
    _create_supplier,
    _dec,
    _login,
    _receive,
    _register_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_org_me_includes_default_threshold(client):
    token = await _register_and_login(client, email="owner19@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get("/orgs/me", headers=headers)
    assert res.status_code == 200
    assert _dec(res.json()["adjust_approval_threshold"]) == Decimal("50")


async def test_owner_can_update_threshold_and_it_takes_effect(client, test_engine):
    token = await _register_and_login(client, email="owner20@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    patch = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "5"}, headers=headers
    )
    assert patch.status_code == 200
    assert _dec(patch.json()["adjust_approval_threshold"]) == Decimal("5")

    await _add_user_with_role(test_engine, org_id, "manager20@example.com", "manager")
    manager_headers = await _login(client, "manager20@example.com")

    # threshold is now 5 - a delta of 10 exceeds it and should queue
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "10", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    assert res.json()["applied"] is False


async def test_manager_cannot_update_threshold(client, test_engine):
    token = await _register_and_login(client, email="owner21@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    await _add_user_with_role(test_engine, org_id, "manager21@example.com", "manager")
    manager_headers = await _login(client, "manager21@example.com")

    res = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "5"}, headers=manager_headers
    )
    assert res.status_code == 403


async def test_negative_threshold_rejected(client):
    token = await _register_and_login(client, email="owner22@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "-1"}, headers=headers
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_org_settings.py -v`
Expected: FAIL - `adjust_approval_threshold` missing from `GET /orgs/me` response, and `PATCH /orgs/me` returns 405 (not implemented).

- [ ] **Step 3: Implement**

Replace the full contents of `backend/app/api/routes/orgs.py`:

```python
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.tenant import Org

router = APIRouter(prefix="/orgs", tags=["orgs"])


class OrgResponse(BaseModel):
    id: str
    name: str
    role: str
    adjust_approval_threshold: Decimal


class OrgUpdateRequest(BaseModel):
    adjust_approval_threshold: Decimal


@router.get("/me", response_model=OrgResponse)
async def get_my_org(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Org).where(Org.id == current.org_id))
    org = result.scalar_one()
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        adjust_approval_threshold=org.adjust_approval_threshold,
    )


@router.patch("/me", response_model=OrgResponse)
async def update_my_org(
    body: OrgUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.adjust_approval_threshold < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "threshold must be zero or positive")
    result = await db.execute(select(Org).where(Org.id == current.org_id))
    org = result.scalar_one()
    org.adjust_approval_threshold = body.adjust_approval_threshold
    await log_event(
        db,
        action="org.threshold_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(org.id),
        metadata={"adjust_approval_threshold": str(body.adjust_approval_threshold)},
    )
    await db.commit()
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        adjust_approval_threshold=org.adjust_approval_threshold,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_org_settings.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `pytest -q`
Expected: PASS. (Check that any pre-existing test reading `/orgs/me`, e.g. inside `test_inventory.py`, still passes now that the response has one more field - additive field, should be unaffected.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/orgs.py backend/tests/test_org_settings.py
git commit -m "feat: add owner-editable adjust_approval_threshold on PATCH /orgs/me"
```

---

### Task 4: Concurrency safety - stale approval after a race

**Files:**
- Modify: `backend/tests/test_concurrency.py`

**Interfaces:**
- Consumes: `_auth`, `_item`, `_supplier`, `_receive`, `PASSWORD`  <!-- pragma: allowlist secret -->, `pg_client`, `pg_engine` fixtures already defined in `test_concurrency.py`; `PATCH /orgs/me` (Task 3) and `POST /inventory/{item_id}/adjust` + approve (Task 2), all read-only usage.

This test only runs against real Postgres (`CONCURRENCY_DATABASE_URL`), same as every other test in this file - it's skipped by default.

- [ ] **Step 1: Write the test**

Add to `backend/tests/test_concurrency.py`:

```python
async def test_stale_approval_after_concurrent_consume_is_rejected(pg_client, pg_engine):
    headers = await _auth(pg_client)
    item = await _item(pg_client, headers)
    supplier = await _supplier(pg_client, headers)
    await _receive(pg_client, headers, item, supplier, 100)

    org_id = (await pg_client.get("/orgs/me", headers=headers)).json()["id"]
    await pg_client.patch("/orgs/me", json={"adjust_approval_threshold": "0"}, headers=headers)

    from sqlalchemy.ext.asyncio import async_sessionmaker
    import uuid as uuid_module

    from app.core.security import hash_password
    from app.models.user import User

    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="manager-stale@example.com",
                password_hash=hash_password(PASSWORD),
                role="manager",
                is_active=True,
            )
        )
        await session.commit()
    login = await pg_client.post(
        "/auth/login", json={"email": "manager-stale@example.com", "password": PASSWORD}
    )
    manager_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Threshold is 0, so any manager adjustment queues, including a negative one.
    submit = await pg_client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert submit.status_code == 200
    request_id = submit.json()["request"]["id"]

    # Meanwhile, 50 units get consumed - only 50 remain, not enough for -60.
    consume = await pg_client.post(
        f"/inventory/{item['id']}/consume", json={"quantity": "50"}, headers=headers
    )
    assert consume.status_code == 200

    approve = await pg_client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=headers
    )
    assert approve.status_code == 409

    # The request is still pending, not silently marked approved on failure.
    pending = await pg_client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    assert any(r["id"] == request_id for r in pending.json())

    detail = await pg_client.get(f"/inventory/{item['id']}", headers=headers)
    assert Decimal(detail.json()["quantity_on_hand"]) == Decimal("50")
```

- [ ] **Step 2: Confirm the skip gate, then run for real**

Without `CONCURRENCY_DATABASE_URL` set:
Run: `pytest tests/test_concurrency.py::test_stale_approval_after_concurrent_consume_is_rejected -v`
Expected: SKIPPED (`CONCURRENCY_DATABASE_URL` not set) - confirms the test is wired into the existing skip gate correctly.

With a real local Postgres available:
Run: `CONCURRENCY_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/evowerk_cloud_mrp pytest tests/test_concurrency.py::test_stale_approval_after_concurrent_consume_is_rejected -v`  <!-- pragma: allowlist secret -->
Expected: PASS on first run - no new guard code is needed, this is a regression test proving the safety property Task 2's `approve_adjustment_request` already provides via the shared guard in `apply_inventory_delta`. If it fails, the bug is almost certainly in Task 2's approve endpoint calling `apply_inventory_delta` in the wrong order relative to setting `req.status` - `req.status = "approved"` must happen *after* the `apply_inventory_delta` call, not before.

- [ ] **Step 3: Run the full concurrency suite against real Postgres**

Run: `CONCURRENCY_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/evowerk_cloud_mrp pytest tests/test_concurrency.py -v`  <!-- pragma: allowlist secret -->
Expected: PASS - all tests in the file, including the pre-existing ones.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_concurrency.py
git commit -m "test: verify a stale adjustment approval is rejected, not silently applied"
```

---

### Task 5: Frontend - Count form, relabeled Adjust form

**Files:**
- Modify: `frontend/src/pages/Inventory.tsx`

**Interfaces:**
- Consumes: `POST /inventory/{item_id}/adjust` and `POST /inventory/{item_id}/count` (Task 2), both returning `AdjustmentOutcome`; `GET /inventory/{item_id}` transactions now include `counted_quantity`, `expected_quantity`, `reason_note`.

No frontend test runner exists in this repo. Verify with `npm run build` (typecheck) and `npm run lint`, plus a manual pass in the dev server.

- [ ] **Step 1: Add reason-code constants, new types, new state, and new mutations**

In `frontend/src/pages/Inventory.tsx`, add after the existing imports:

```typescript
const REASON_CODES = [
  { value: "cycle_count", label: "Cycle count" },
  { value: "damage", label: "Damage" },
  { value: "found_stock", label: "Found stock" },
  { value: "shrinkage", label: "Shrinkage" },
  { value: "correction", label: "Correction" },
  { value: "other", label: "Other" },
] as const;
type ReasonCode = (typeof REASON_CODES)[number]["value"];
```

Update `InventoryTransaction` to add the three new fields:

```typescript
interface InventoryTransaction {
  id: string;
  location_code: string;
  transaction_type: string;
  quantity_delta: string;
  reason: string | null;
  counted_quantity: string | null;
  expected_quantity: string | null;
  reason_note: string | null;
  created_at: string;
}
```

Add new interfaces right after `InventoryAnalyticsLine`:

```typescript
interface AdjustmentRequestSummary {
  id: string;
  status: string;
}

interface AdjustmentOutcome {
  applied: boolean;
  item: InventoryItem | null;
  request: AdjustmentRequestSummary | null;
}
```

Inside `InventoryPanel`, replace the existing state block:

```typescript
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [consumeQty, setConsumeQty] = useState("1");
  const [consumeLoc, setConsumeLoc] = useState("");
  const [adjustDelta, setAdjustDelta] = useState("1");
  const [adjustLoc, setAdjustLoc] = useState("");
  const [adjustReasonCode, setAdjustReasonCode] = useState<ReasonCode>("correction");
  const [adjustReasonNote, setAdjustReasonNote] = useState("");
  const [countQty, setCountQty] = useState("");
  const [countLoc, setCountLoc] = useState("");
  const [countReasonCode, setCountReasonCode] = useState<ReasonCode>("cycle_count");
  const [countReasonNote, setCountReasonNote] = useState("");
  const [fromLoc, setFromLoc] = useState("");
  const [toLoc, setToLoc] = useState("");
  const [transferQty, setTransferQty] = useState("1");
  const [error, setError] = useState<string | null>(null);
  const [pendingNotice, setPendingNotice] = useState<string | null>(null);
```

Replace the existing `adjust` mutation and add the `count` mutation:

```typescript
  const adjust = useMutation<AdjustmentOutcome, Error>({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/adjust`, {
        quantity: adjustDelta,
        location_id: adjustLoc || null,
        reason_code: adjustReasonCode,
        reason_note: adjustReasonCode === "other" ? adjustReasonNote : null,
      })(),
    onSuccess: (outcome) => {
      if (outcome.applied) {
        invalidate();
      } else {
        setPendingNotice(`Submitted for owner approval (request ${outcome.request?.id.slice(0, 8)}…).`);
      }
    },
    onError: (e: Error) => setError(e.message),
  });

  const count = useMutation<AdjustmentOutcome, Error>({
    mutationFn: () =>
      post(`/inventory/${selectedItemId}/count`, {
        location_id: countLoc || null,
        counted_quantity: countQty,
        reason_code: countReasonCode,
        reason_note: countReasonCode === "other" ? countReasonNote : null,
      })(),
    onSuccess: (outcome) => {
      if (outcome.applied) {
        invalidate();
      } else {
        setPendingNotice(`Submitted for owner approval (request ${outcome.request?.id.slice(0, 8)}…).`);
      }
      setCountQty("");
    },
    onError: (e: Error) => setError(e.message),
  });
```

- [ ] **Step 2: Update the JSX - error/pending banners, and the four-form grid**

Right after the existing `{error && (...)}` block, add:

```tsx
      {pendingNotice && (
        <Alert variant="info" className="mb-6">
          {pendingNotice}
        </Alert>
      )}
```

Replace the `<div className="grid gap-4 md:grid-cols-3">` block (the one containing the Consume, Adjust, and Transfer forms) with a four-form version - Consume unchanged, Count new, Adjust relabeled with reason fields, Transfer unchanged:

```tsx
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  setPendingNotice(null);
                  consume.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Consume</p>
                <Field label="Quantity" htmlFor="c-qty">
                  <Input
                    id="c-qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={consumeQty}
                    onChange={(e) => setConsumeQty(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="c-loc">
                  <Select id="c-loc" value={consumeLoc} onChange={(e) => setConsumeLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Button type="submit" variant="secondary" className="w-full" loading={consume.isPending}>
                  Consume
                </Button>
              </form>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  setPendingNotice(null);
                  count.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Count</p>
                <Field label="Counted quantity" htmlFor="cnt-qty">
                  <Input
                    id="cnt-qty"
                    type="number"
                    step="0.0001"
                    min="0"
                    value={countQty}
                    onChange={(e) => setCountQty(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="cnt-loc">
                  <Select id="cnt-loc" value={countLoc} onChange={(e) => setCountLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Field label="Reason" htmlFor="cnt-reason">
                  <Select
                    id="cnt-reason"
                    value={countReasonCode}
                    onChange={(e) => setCountReasonCode(e.target.value as ReasonCode)}
                  >
                    {REASON_CODES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {countReasonCode === "other" && (
                  <Field label="Note" htmlFor="cnt-note">
                    <Input
                      id="cnt-note"
                      value={countReasonNote}
                      onChange={(e) => setCountReasonNote(e.target.value)}
                      required
                    />
                  </Field>
                )}
                <Button type="submit" variant="secondary" className="w-full" loading={count.isPending}>
                  Count
                </Button>
              </form>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setError(null);
                  setPendingNotice(null);
                  adjust.mutate();
                }}
                className="space-y-3 rounded-lg border border-border p-4"
              >
                <p className="text-sm font-semibold">Adjust (no count)</p>
                <Field label="Delta (+/-)" htmlFor="a-qty">
                  <Input
                    id="a-qty"
                    type="number"
                    step="0.0001"
                    value={adjustDelta}
                    onChange={(e) => setAdjustDelta(e.target.value)}
                    required
                  />
                </Field>
                <Field label="At location" htmlFor="a-loc">
                  <Select id="a-loc" value={adjustLoc} onChange={(e) => setAdjustLoc(e.target.value)}>
                    {locationOptions}
                  </Select>
                </Field>
                <Field label="Reason" htmlFor="a-reason">
                  <Select
                    id="a-reason"
                    value={adjustReasonCode}
                    onChange={(e) => setAdjustReasonCode(e.target.value as ReasonCode)}
                  >
                    {REASON_CODES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {adjustReasonCode === "other" && (
                  <Field label="Note" htmlFor="a-note">
                    <Input
                      id="a-note"
                      value={adjustReasonNote}
                      onChange={(e) => setAdjustReasonNote(e.target.value)}
                      required
                    />
                  </Field>
                )}
                <Button type="submit" variant="secondary" className="w-full" loading={adjust.isPending}>
                  Adjust
                </Button>
              </form>

              <form onSubmit={onTransfer} className="space-y-3 rounded-lg border border-border p-4">
                <p className="text-sm font-semibold">Transfer</p>
                <Field label="Quantity" htmlFor="t-qty">
                  <Input
                    id="t-qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={transferQty}
                    onChange={(e) => setTransferQty(e.target.value)}
                    required
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="From" htmlFor="t-from">
                    <Select id="t-from" value={fromLoc} onChange={(e) => setFromLoc(e.target.value)}>
                      <option value="">Select…</option>
                      {locations.data?.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="To" htmlFor="t-to">
                    <Select id="t-to" value={toLoc} onChange={(e) => setToLoc(e.target.value)}>
                      <option value="">Select…</option>
                      {locations.data?.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <Button type="submit" variant="secondary" className="w-full" loading={transfer.isPending}>
                  Transfer
                </Button>
              </form>
            </div>
```

- [ ] **Step 3: Show the variance inline in the transaction history table**

In the transactions `<TBody>` map, replace the `<TD className="text-muted-foreground">{tx.reason ?? "—"}</TD>` cell with:

```tsx
                      <TD className="text-muted-foreground">
                        {tx.reason ? humanize(tx.reason) : "—"}
                        {tx.counted_quantity != null && (
                          <span className="block text-xs">
                            counted {tx.counted_quantity} · expected {tx.expected_quantity}
                          </span>
                        )}
                      </TD>
```

- [ ] **Step 4: Run typecheck and lint**

Run (from `frontend/`): `npm run build`
Expected: PASS - no type errors.

Run: `npm run lint`
Expected: PASS

- [ ] **Step 5: Manual verification in the dev server**

Run: `npm run dev` (from `frontend/`), with the backend running against a DB that has Tasks 1-4 applied.
- Log in as an owner, go to Warehouse → Inventory, select an item with on-hand stock.
- Submit a Count with a different quantity than shown - confirm on-hand updates and the transaction history row shows "counted X · expected Y".
- Submit an Adjust with reason "Other" and no note - confirm the browser blocks submit (native `required`) or the API returns a validation error surfaced in the error banner.
- Submit an Adjust with a valid reason - confirm it applies immediately (owner never queues).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Inventory.tsx
git commit -m "feat: add Count form and reason codes to the Warehouse Inventory panel"
```

---

### Task 6: Frontend - Approvals tab and panel

**Files:**
- Create: `frontend/src/pages/Approvals.tsx`
- Modify: `frontend/src/pages/Warehouse.tsx`

**Interfaces:**
- Consumes: `GET /inventory/adjustment-requests?status=pending`, `POST /inventory/adjustment-requests/{id}/approve`, `POST /inventory/adjustment-requests/{id}/reject` (Task 2); `GET /orgs/me` (now includes `adjust_approval_threshold`) and `PATCH /orgs/me` (Task 3).
- Produces: `ApprovalsPanel` component, exported from `frontend/src/pages/Approvals.tsx`, imported by `Warehouse.tsx` exactly like `InventoryPanel`/`LocationsPanel` already are.

- [ ] **Step 1: Create the Approvals panel**

Create `frontend/src/pages/Approvals.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { ClipboardCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface OrgMeResponse {
  id: string;
  name: string;
  role: string;
  adjust_approval_threshold: string;
}

interface AdjustmentRequest {
  id: string;
  item_sku: string;
  location_code: string;
  quantity_delta: string;
  counted_quantity: string | null;
  expected_quantity_at_submit: string;
  reason_code: string;
  reason_note: string | null;
  status: string;
  requested_at: string;
}

export function ApprovalsPanel() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState("");
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});

  const org = useQuery<OrgMeResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
  const isOwner = org.data?.role === "owner";

  const requests = useQuery<AdjustmentRequest[]>({
    queryKey: ["inventory-adjustment-requests"],
    queryFn: async () => {
      const res = await apiFetch("/inventory/adjustment-requests?status=pending");
      if (!res.ok) throw new Error("Failed to load pending approvals");
      return res.json();
    },
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["inventory-adjustment-requests"] });
    queryClient.invalidateQueries({ queryKey: ["inventory"] });
  }

  const approve = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/inventory/adjustment-requests/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ review_note: reviewNotes[id] || null }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Approve failed");
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const reject = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/inventory/adjustment-requests/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ review_note: reviewNotes[id] || null }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Reject failed");
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const updateThreshold = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/orgs/me", {
        method: "PATCH",
        body: JSON.stringify({ adjust_approval_threshold: threshold }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Failed to update threshold");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-me"] });
      setThreshold("");
    },
    onError: (e: Error) => setError(e.message),
  });

  function onThresholdSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    updateThreshold.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {isOwner && (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Approval threshold</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onThresholdSubmit} className="flex items-end gap-4">
              <Field
                label="Quantity"
                htmlFor="threshold"
                hint={`Current: ${org.data?.adjust_approval_threshold ?? "—"}. Manager adjustments/counts above this need approval.`}
              >
                <Input
                  id="threshold"
                  type="number"
                  step="0.0001"
                  min="0"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  placeholder={org.data?.adjust_approval_threshold}
                />
              </Field>
              <Button type="submit" loading={updateThreshold.isPending}>
                Save
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {requests.data && requests.data.length === 0 ? (
        <EmptyState icon={<ClipboardCheck />} title="No pending approvals" />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Location</TH>
                <TH>Delta</TH>
                <TH>Counted / Expected</TH>
                <TH>Reason</TH>
                <TH>Requested</TH>
                {isOwner && <TH>Actions</TH>}
              </TR>
            </THead>
            <TBody>
              {requests.data?.map((req) => (
                <TR key={req.id}>
                  <TD className="font-medium">{req.item_sku}</TD>
                  <TD className="text-muted-foreground">{req.location_code}</TD>
                  <TD>{req.quantity_delta}</TD>
                  <TD className="text-muted-foreground">
                    {req.counted_quantity != null
                      ? `${req.counted_quantity} / ${req.expected_quantity_at_submit}`
                      : "—"}
                  </TD>
                  <TD className="text-muted-foreground">
                    {req.reason_code}
                    {req.reason_note ? `: ${req.reason_note}` : ""}
                  </TD>
                  <TD className="text-muted-foreground">
                    {new Date(req.requested_at).toLocaleString()}
                  </TD>
                  {isOwner && (
                    <TD>
                      <div className="flex items-center gap-2">
                        <Input
                          placeholder="Note (optional)"
                          className="h-8 w-36 text-xs"
                          value={reviewNotes[req.id] ?? ""}
                          onChange={(e) =>
                            setReviewNotes((prev) => ({ ...prev, [req.id]: e.target.value }))
                          }
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={approve.isPending}
                          onClick={() => approve.mutate(req.id)}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          loading={reject.isPending}
                          onClick={() => reject.mutate(req.id)}
                        >
                          Reject
                        </Button>
                      </div>
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}
```

If `ClipboardCheck` isn't exported by the installed `lucide-react` version, swap it for another already-used icon (e.g. `CheckCircle2`, already imported elsewhere in this codebase) - don't add a new icon library.

- [ ] **Step 2: Wire the Approvals tab into Warehouse.tsx**

Replace the full contents of `frontend/src/pages/Warehouse.tsx`:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { InventoryPanel } from "./Inventory";
import { LocationsPanel } from "./Locations";
import { ApprovalsPanel } from "./Approvals";

interface OrgMeResponse {
  role: string;
}

const BASE_TABS = [
  { value: "inventory", label: "Inventory" },
  { value: "locations", label: "Locations" },
] as const;

const APPROVALS_TAB = { value: "approvals", label: "Approvals" } as const;

type Tab = "inventory" | "locations" | "approvals";

export function Warehouse() {
  const [tab, setTab] = useState<Tab>("inventory");

  const org = useQuery<OrgMeResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
  const canSeeApprovals = org.data?.role === "owner" || org.data?.role === "manager";
  const tabs = canSeeApprovals ? [...BASE_TABS, APPROVALS_TAB] : BASE_TABS;

  return (
    <div>
      <PageHeader
        title="Warehouse"
        description="On-hand stock by location, and the locations that hold it. Receiving a PO adds stock; consume, adjust, or transfer to move it. (Lot-tracked items track on-hand through their lots.)"
        actions={<PageTabs tabs={tabs} active={tab} onChange={setTab} />}
      />
      {tab === "inventory" && <InventoryPanel />}
      {tab === "locations" && <LocationsPanel />}
      {tab === "approvals" && canSeeApprovals && <ApprovalsPanel />}
    </div>
  );
}
```

- [ ] **Step 3: Run typecheck and lint**

Run (from `frontend/`): `npm run build`
Expected: PASS

Run: `npm run lint`
Expected: PASS

- [ ] **Step 4: Manual verification in the dev server**

Run: `npm run dev` (from `frontend/`), backend running with all backend tasks applied.
- As owner: go to Warehouse, confirm the "Approvals" tab is visible. Submit a manager-role over-threshold adjustment (log in as a manager in another session/incognito window, or promote a test user's role directly in the DB), confirm it shows up in the queue, approve it, confirm on-hand updates and it drops off the pending list.
- As owner: edit the threshold value, save, confirm `GET /orgs/me` reflects the new value on reload.
- As manager: confirm the "Approvals" tab is visible but has no Approve/Reject buttons or threshold editor.
- As a `member`-role user: confirm the "Approvals" tab does not appear at all.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Approvals.tsx frontend/src/pages/Warehouse.tsx
git commit -m "feat: add Approvals tab to the Warehouse page"
```

---

## Self-Review Notes

**Spec coverage:** manager role (Task 2), reason-code vocabulary (Task 2), org threshold (Task 1, Task 3), pending-approval queue (Task 2), count entry point (Task 2), raw adjust kept for paperwork corrections (Task 2, unchanged endpoint), frontend Count/Adjust forms (Task 5), Approvals tab (Task 6), stale-approval concurrency guarantee (Task 4). All spec sections have a covering task.

**One-file-one-task check:** `inventory.py` (routes) - Task 2 only. `test_inventory.py` - Task 2 only. `test_inventory_adjustment_requests.py` - Task 2 only (created whole). `orgs.py` - Task 3 only. `test_org_settings.py` - Task 3 only (created whole). `test_concurrency.py` - Task 4 only. `Inventory.tsx` - Task 5 only. `Approvals.tsx` / `Warehouse.tsx` - Task 6 only. `tenant.py` / `inventory.py` (models) / `services/inventory.py` / the migration - Task 1 only. No file appears in two tasks.

**Type consistency:** `_post_or_queue_adjustment` is defined once (Task 2) and called identically by `adjust_inventory` and `count_inventory` in that same task - same parameter names throughout. `AdjustmentOutcome`/`AdjustmentRequestResponse` field names match between backend Pydantic models (Task 2) and frontend TypeScript interfaces (Tasks 5-6): `applied`, `item`, `request`, `quantity_delta`, `counted_quantity`, `expected_quantity_at_submit`, `reason_code`, `reason_note`, `status`, `requested_at`, `reviewed_by`, `reviewed_at`, `review_note`.

**Placeholder scan:** no TBD/TODO markers; every step has concrete code, exact commands, and expected output.
