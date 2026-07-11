from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lot import Lot

# Two concurrent receipts/transfers could both try to create the same new
# (item, lot_number, location) row. Each attempt runs in its own SAVEPOINT so a
# collision only rolls back this one insert, not the caller's other pending
# writes (find_or_create_lot runs inside receive_purchase_order's per-line loop
# and inside transfer_lot's multi-step write).
MAX_LOT_CREATE_ATTEMPTS = 3


async def topup_lot(db: AsyncSession, lot: Lot, quantity: Decimal, expiry_date: date | None) -> Lot:
    """Guarded UPDATE, not read-then-`+=`-then-write: two concurrent credits to
    the same lot (e.g. two shipments of one physical batch landing close
    together, or a receipt racing a transfer-in) could otherwise both read the
    same starting quantity and whichever flush lands second would silently
    overwrite the first's increment instead of adding to it.
    """
    values: dict[str, object] = {
        "quantity_received": Lot.quantity_received + quantity,
        "quantity_remaining": Lot.quantity_remaining + quantity,
        "status": case((Lot.status == "depleted", "active"), else_=Lot.status),
    }
    if expiry_date is not None:
        values["expiry_date"] = case((Lot.expiry_date.is_(None), expiry_date), else_=Lot.expiry_date)
    result = await db.execute(
        update(Lot)
        .where(Lot.id == lot.id)
        .values(**values)
        .returning(Lot.quantity_received, Lot.quantity_remaining, Lot.status, Lot.expiry_date)
    )
    lot.quantity_received, lot.quantity_remaining, lot.status, lot.expiry_date = result.one()
    return lot


async def _topup_checked(db: AsyncSession, lot: Lot, quantity: Decimal, expiry_date: date | None) -> Lot:
    """Shared by find_or_create_lot's fast path and its concurrent-creation
    retry, so the expiry-mismatch guard applies the same way regardless of
    which path found the existing lot.
    """
    if expiry_date is not None and lot.expiry_date is not None and lot.expiry_date != expiry_date:
        # A mismatch is a strong signal of a mistyped lot_number colliding
        # with a genuinely different batch - silently accepting it would
        # merge two physical batches under one Lot row.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Lot {lot.lot_number} already has a different expiry date on file for this item at this "
            "location - check for a lot_number typo before retrying",
        )
    return await topup_lot(db, lot, quantity, expiry_date)


async def find_or_create_lot(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    lot_number: str,
    location_id: uuid.UUID,
    quantity: Decimal,
    expiry_date: date | None,
    created_by: uuid.UUID,
) -> Lot:
    """A supplier's single physical batch is often delivered across multiple
    partial shipments/POs, or moved between locations, so a repeat lot_number
    for the same item+location tops up the existing Lot rather than being
    rejected as a duplicate. The same lot_number at a *different* location is a
    separate Lot row - each location's on-hand for that batch is tracked
    independently (see Lot.location_id).
    """
    result = await db.execute(
        select(Lot).where(
            Lot.org_id == org_id,
            Lot.item_id == item_id,
            Lot.lot_number == lot_number,
            Lot.location_id == location_id,
        )
    )
    lot = result.scalar_one_or_none()
    if lot is not None:
        return await _topup_checked(db, lot, quantity, expiry_date)

    for attempt in range(MAX_LOT_CREATE_ATTEMPTS):
        try:
            async with db.begin_nested():
                new_lot = Lot(
                    org_id=org_id,
                    item_id=item_id,
                    lot_number=lot_number,
                    location_id=location_id,
                    quantity_received=quantity,
                    quantity_remaining=quantity,
                    status="active",
                    expiry_date=expiry_date,
                    created_by=created_by,
                )
                db.add(new_lot)
                await db.flush()
            return new_lot
        except IntegrityError:
            # Savepoint rolled back automatically - the rest of this request's
            # pending changes are untouched. Someone else created this
            # lot_number/location concurrently; top it up instead of erroring.
            result = await db.execute(
                select(Lot).where(
                    Lot.org_id == org_id,
                    Lot.item_id == item_id,
                    Lot.lot_number == lot_number,
                    Lot.location_id == location_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return await _topup_checked(db, existing, quantity, expiry_date)
            if attempt == MAX_LOT_CREATE_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, f"Could not allocate lot {lot_number}, please retry"
                ) from None
    raise AssertionError("unreachable")


async def consume_lot_guarded(db: AsyncSession, lot: Lot, quantity: Decimal) -> Lot:
    """Guarded UPDATE, not read-then-check-then-write: two concurrent debits
    against the same lot (a consume racing a transfer-out) could otherwise both
    read the same quantity_remaining, both pass validation, and leave it
    negative. The WHERE clause re-checks status/quantity atomically against
    whatever the row actually holds at update time, not the Python-side value
    read by the caller.
    """
    new_remaining_expr = Lot.quantity_remaining - quantity
    result = await db.execute(
        update(Lot)
        .where(Lot.id == lot.id, Lot.quantity_remaining >= quantity, Lot.status != "on_hold")
        .values(
            quantity_remaining=new_remaining_expr,
            status=case((new_remaining_expr <= 0, "depleted"), else_="active"),
        )
        .returning(Lot.quantity_remaining, Lot.status)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Moving {quantity} would exceed the quantity remaining, or the lot was put on hold "
            "concurrently",
        )
    lot.quantity_remaining, lot.status = row
    return lot


@dataclass
class LotConsumption:
    lot: Lot
    quantity: Decimal


async def consume_lot_fefo(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity: Decimal,
) -> list[LotConsumption]:
    """Consume `quantity` of an item at a location across as many active,
    unheld lots as needed - earliest-expiring first (lots with no expiry
    date last), oldest-received as the tiebreak - so a caller who just wants
    "the right stock to use next" doesn't have to pick a specific lot.
    Raises 409 and writes nothing if the location can't cover the full
    request (each individual debit is still guarded via consume_lot_guarded,
    so a concurrent change to a candidate lot surfaces as the same 409
    rather than overselling it).
    """
    result = await db.execute(
        select(Lot)
        .where(
            Lot.org_id == org_id,
            Lot.item_id == item_id,
            Lot.location_id == location_id,
            Lot.status == "active",
            Lot.quantity_remaining > 0,
        )
        .order_by(Lot.expiry_date.is_(None), Lot.expiry_date, Lot.created_at)
    )
    candidates = result.scalars().all()

    remaining = quantity
    consumed: list[LotConsumption] = []
    for lot in candidates:
        if remaining <= 0:
            break
        take = min(lot.quantity_remaining, remaining)
        await consume_lot_guarded(db, lot, take)
        consumed.append(LotConsumption(lot=lot, quantity=take))
        remaining -= take

    if remaining > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only {quantity - remaining} of {quantity} available as active, unheld stock at this location",
        )
    return consumed
