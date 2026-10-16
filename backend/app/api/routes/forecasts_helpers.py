"""Schemas and shared helpers for the /forecasts and /mrp routes (split out of forecasts.py)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forecast import DemandForecast
from app.services.demand_forecasting import (
    DEFAULT_HORIZON_DAYS,
)
from app.services.mrp import MrpPlanLine

# A repeat import of the same period is an upsert, so the only real collision is
# two *first* inserts of the same (item, period) racing. One retry after
# re-selecting is enough - by then the winning row is committed and visible.
MAX_FORECAST_UPSERT_ATTEMPTS = 3


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ForecastUpsertRequest(BaseModel):
    item_id: str
    period_start: date
    quantity: Decimal
    notes: str | None = None


class ForecastImportRow(BaseModel):
    item_id: str
    period_start: date
    quantity: Decimal
    notes: str | None = None


class ForecastImportResponse(BaseModel):
    created: int
    updated: int
    total: int


class SuggestedForecastPoint(BaseModel):
    period_start: date
    quantity: Decimal
    lower: Decimal | None
    upper: Decimal | None


class SuggestedForecastResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    # moving_avg | exp_smoothing | no_data - see services.demand_forecasting.
    method_used: str
    # Non-zero consumption days found in the lookback window - a rough
    # confidence signal for how much this projection should be trusted.
    history_points: int
    points: list[SuggestedForecastPoint]


class BulkSuggestedResponse(BaseModel):
    # Suggestion window, inclusive both ends: [as_of + 1, as_of + horizon_days].
    # The accept route deletes existing rows in exactly this window, so the
    # client can label "replaces N rows" from the same dates it displays.
    window_start: date
    window_end: date
    # Same per-item shape as the single-item route, but points are weekly
    # buckets (see services.demand_forecasting.bucket_weekly), not days.
    items: list[SuggestedForecastResponse]


class AcceptSuggestedRequest(BaseModel):
    item_ids: list[str]
    horizon_days: int = DEFAULT_HORIZON_DAYS


class SkippedSuggestion(BaseModel):
    item_id: str
    item_sku: str


class AcceptSuggestedResponse(BaseModel):
    items: int  # items actually written
    deleted: int
    created: int
    updated: int
    # Requested items that had no consumption history at accept time; their
    # existing forecast rows are left untouched.
    skipped: list[SkippedSuggestion]


class MrpPlanResponse(BaseModel):
    horizon_start: date
    horizon_end: date
    lines: list[MrpPlanLine]


class GeneratePoRequest(BaseModel):
    supplier_id: str
    # Which shortfall items to put on this supplier's PO. Since items have no
    # stored source-of-supply yet, the caller chooses the supplier and the
    # subset of items - one call makes one draft PO for one supplier.
    item_ids: list[str]
    horizon_start: date | None = None
    horizon_end: date | None = None


class GeneratePoLine(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    quantity_ordered: Decimal
    unit_price: Decimal


class GeneratePoResponse(BaseModel):
    po_id: str
    po_number: int
    supplier_id: str
    supplier_name: str
    lines: list[GeneratePoLine]


class GeneratePosRequest(BaseModel):
    horizon_start: date | None = None
    horizon_end: date | None = None


class GeneratedPoSummary(BaseModel):
    po_id: str
    po_number: int
    supplier_id: str
    supplier_name: str
    line_count: int


class UnassignedItem(BaseModel):
    # A purchased shortfall with no preferred supplier - nothing to auto-order it
    # against, so it's reported for the user to assign a supplier (or use the
    # manual generate-po).
    item_id: str
    item_sku: str
    item_name: str
    net_requirement: Decimal


class GeneratePosResponse(BaseModel):
    created: list[GeneratedPoSummary]
    unassigned: list[UnassignedItem]


class BuildPlanRequest(BaseModel):
    item_id: str
    build_quantity: Decimal
    yield_loss_pct: Decimal = Decimal(0)


class BuildPlanResponse(BaseModel):
    item_id: str
    item_sku: str
    item_name: str
    build_quantity: Decimal
    yield_loss_pct: Decimal
    lines: list[MrpPlanLine]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _suggestion_to_response(s) -> SuggestedForecastResponse:
    return SuggestedForecastResponse(
        item_id=str(s.item_id),
        item_sku=s.item_sku,
        item_name=s.item_name,
        method_used=s.method_used,
        history_points=s.history_points,
        points=[
            SuggestedForecastPoint(
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                lower=Decimal(str(round(p.lower, 4))) if p.lower is not None else None,
                upper=Decimal(str(round(p.upper, 4))) if p.upper is not None else None,
            )
            for p in s.points
        ],
    )


def _parse_item_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {raw}") from None


async def _upsert_forecast(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    period_start: date,
    quantity: Decimal,
    notes: str | None,
    source: str,
    created_by: uuid.UUID,
) -> tuple[DemandForecast, bool]:
    """Set-not-increment upsert on (org, item, period). Returns (row, created).

    A forecast import *overwrites* the number for a period (re-running the
    desktop forecaster should replace the stale value, never add to it), so
    unlike lot top-up there's no read-modify-write increment to lose - a
    concurrent double-import of the same period is a last-writer-wins overwrite,
    which is the intended semantics. The only race we guard is two *first*
    inserts of the same period colliding on the unique constraint, handled the
    same way _find_or_create_lot handles a concurrent lot creation: a per-row
    SAVEPOINT so a rollback here can't discard the other rows of a bulk import.
    """
    existing = (
        await db.execute(
            select(DemandForecast).where(
                DemandForecast.org_id == org_id,
                DemandForecast.item_id == item_id,
                DemandForecast.period_start == period_start,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.quantity = quantity
        existing.notes = notes
        existing.source = source
        await db.flush()
        return existing, False

    for attempt in range(MAX_FORECAST_UPSERT_ATTEMPTS):
        try:
            async with db.begin_nested():
                fc = DemandForecast(
                    org_id=org_id,
                    item_id=item_id,
                    period_start=period_start,
                    quantity=quantity,
                    notes=notes,
                    source=source,
                    created_by=created_by,
                )
                db.add(fc)
                await db.flush()
            return fc, True
        except IntegrityError:
            # Savepoint rolled back automatically; the rest of a bulk import's
            # pending rows are untouched. Someone inserted this period
            # concurrently - overwrite it instead of erroring.
            existing = (
                await db.execute(
                    select(DemandForecast).where(
                        DemandForecast.org_id == org_id,
                        DemandForecast.item_id == item_id,
                        DemandForecast.period_start == period_start,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.quantity = quantity
                existing.notes = notes
                existing.source = source
                await db.flush()
                return existing, False
            if attempt == MAX_FORECAST_UPSERT_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Could not save forecast, please retry",
                ) from None
    raise AssertionError("unreachable")
