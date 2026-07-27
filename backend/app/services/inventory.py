from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryBalance, InventoryTransaction
from app.services.locations import location_code

# Several concurrent *first* movements for the same item+location can all try to
# create its balance row and collide on uq_inventory_balances_org_item_loc. Each
# loser retries the guarded UPDATE (the row now exists); a few extra attempts
# absorb a burst of concurrent first-creates before giving up with a retry-able 409.
MAX_BALANCE_CREATE_ATTEMPTS = 5


class InsufficientInventory(HTTPException):
    """Raised when a delta would drive on-hand below zero. A 409 (conflict with
    current stock), not a 400 - the request is well-formed, it just lost a race
    or asked for more than exists."""

    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_409_CONFLICT, detail)


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


async def _bump_balance(
    db: AsyncSession, *, org_id: uuid.UUID, item_id: uuid.UUID, location_id: uuid.UUID, delta: Decimal
) -> Decimal:
    new_expr = InventoryBalance.quantity_on_hand + delta
    stmt = (
        update(InventoryBalance)
        .where(
            InventoryBalance.org_id == org_id,
            InventoryBalance.item_id == item_id,
            InventoryBalance.location_id == location_id,
        )
        .values(quantity_on_hand=new_expr)
        .returning(InventoryBalance.quantity_on_hand)
    )
    if delta < 0:
        stmt = stmt.where(new_expr >= 0)

    for _ in range(MAX_BALANCE_CREATE_ATTEMPTS):
        # 1. Try to update an existing balance row atomically.
        row = (await db.execute(stmt)).first()
        if row is not None:
            return row[0]

        # 2. No row updated: either the balance doesn't exist yet at this
        # location, or it exists but the non-negative guard blocked the update.
        # Disambiguate.
        existing = (
            await db.execute(
                select(InventoryBalance.quantity_on_hand).where(
                    InventoryBalance.org_id == org_id,
                    InventoryBalance.item_id == item_id,
                    InventoryBalance.location_id == location_id,
                )
            )
        ).scalar_one_or_none()
        # The location lookup sits inside the failure branch so the happy path
        # takes no extra query for a string it never builds.
        if existing is not None:
            code = await location_code(db, location_id)
            raise InsufficientInventory(
                f"On-hand at {code} is {existing}; that movement would drive it below zero"
            )
        if delta < 0:
            code = await location_code(db, location_id)
            raise InsufficientInventory(f"Nothing on hand at {code} to consume")

        # 3. First movement for this item+location (delta > 0): create the
        # balance. If a concurrent request won the race, loop back and update
        # instead - by then its row is committed and the guarded UPDATE in step 1
        # will find it.
        try:
            async with db.begin_nested():
                db.add(
                    InventoryBalance(
                        org_id=org_id,
                        item_id=item_id,
                        location_id=location_id,
                        quantity_on_hand=delta,
                    )
                )
                await db.flush()
            return delta
        except IntegrityError:
            continue

    raise InsufficientInventory("Could not update on-hand, please retry")
