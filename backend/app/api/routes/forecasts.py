from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.forecast import DemandForecast
from app.services.demand_forecasting import (
    DEFAULT_HORIZON_DAYS,
    compute_demand_forecast,
    compute_weekly_suggestions,
)
from app.api.routes.forecasts_helpers import (
    ForecastUpsertRequest,
    ForecastImportRow,
    ForecastImportResponse,
    SuggestedForecastPoint,
    SuggestedForecastResponse,
    BulkSuggestedResponse,
    AcceptSuggestedRequest,
    SkippedSuggestion,
    AcceptSuggestedResponse,
    _suggestion_to_response,
    _parse_item_id,
    _upsert_forecast,
)
# mrp_router lives in forecasts_mrp; re-exported so app.main keeps including forecasts.mrp_router.
from app.api.routes.forecasts_mrp import mrp_router  # noqa: F401

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


# Stays in this module: another ForecastResponse exists elsewhere, so OpenAPI names this
# schema by module path (app__api__routes__forecasts__ForecastResponse).
class ForecastResponse(BaseModel):
    id: str
    item_id: str
    item_sku: str
    item_name: str
    period_start: date
    quantity: Decimal
    source: str
    notes: str | None
    updated_at: datetime


def _to_response(fc: DemandForecast, item: Item) -> ForecastResponse:
    return ForecastResponse(
        id=str(fc.id),
        item_id=str(fc.item_id),
        item_sku=item.sku,
        item_name=item.name,
        period_start=fc.period_start,
        quantity=fc.quantity,
        source=fc.source,
        notes=fc.notes,
        updated_at=fc.updated_at,
    )


# --------------------------------------------------------------------------- #
# Forecast CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ForecastResponse])
async def list_forecasts(
    item_id: str | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(DemandForecast, Item)
        .join(Item, Item.id == DemandForecast.item_id)
        .where(DemandForecast.org_id == current.org_id)
    )
    if item_id:
        query = query.where(DemandForecast.item_id == _parse_item_id(item_id))
    result = await db.execute(query.order_by(Item.sku, DemandForecast.period_start))
    return [_to_response(fc, item) for fc, item in result.all()]


@router.post("", response_model=ForecastResponse)
async def upsert_forecast(
    body: ForecastUpsertRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.quantity < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quantity must be non-negative")
    item = await get_or_404(db, Item, current.org_id, _parse_item_id(body.item_id), "Item not found")

    fc, created = await _upsert_forecast(
        db,
        org_id=current.org_id,
        item_id=item.id,
        period_start=body.period_start,
        quantity=body.quantity,
        notes=body.notes,
        source="manual",
        created_by=current.id,
    )
    await log_event(
        db,
        action="forecast.upserted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(fc.id),
        metadata={
            "item_id": str(item.id),
            "period_start": body.period_start.isoformat(),
            "quantity": str(body.quantity),
            "created": created,
        },
    )
    await db.commit()
    return _to_response(fc, item)


@router.post("/import", response_model=ForecastImportResponse)
async def import_forecasts(
    rows: list[ForecastImportRow],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk upsert, source stamped "imported" - the integration point for the
    desktop forecaster's output. Whole request is atomic: any bad row (unknown
    item, negative quantity) rejects the entire batch so a partial import can't
    leave a half-refreshed plan.
    """
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No forecast rows provided")

    # Validate every row up front, before any write, so validation failure
    # can't leave some rows already upserted.
    parsed: list[tuple[uuid.UUID, date, Decimal, str | None]] = []
    item_ids: set[uuid.UUID] = set()
    for row in rows:
        if row.quantity < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"quantity must be non-negative (item {row.item_id})"
            )
        iid = _parse_item_id(row.item_id)
        item_ids.add(iid)
        parsed.append((iid, row.period_start, row.quantity, row.notes))

    # One query to confirm every referenced item belongs to this org.
    found = (
        await db.execute(select(Item.id).where(Item.org_id == current.org_id, Item.id.in_(item_ids)))
    ).scalars().all()
    missing = item_ids - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )

    created = updated = 0
    for iid, period_start, quantity, notes in parsed:
        _, was_created = await _upsert_forecast(
            db,
            org_id=current.org_id,
            item_id=iid,
            period_start=period_start,
            quantity=quantity,
            notes=notes,
            source="imported",
            created_by=current.id,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    await log_event(
        db,
        action="forecast.imported",
        org_id=current.org_id,
        actor_user_id=current.id,
        metadata={"created": created, "updated": updated, "total": len(parsed)},
    )
    await db.commit()
    return ForecastImportResponse(created=created, updated=updated, total=len(parsed))


@router.delete("/{forecast_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_forecast(
    forecast_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    fc = (
        await db.execute(
            select(DemandForecast).where(
                DemandForecast.id == forecast_id, DemandForecast.org_id == current.org_id
            )
        )
    ).scalar_one_or_none()
    if fc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Forecast not found")
    await db.delete(fc)
    await log_event(
        db,
        action="forecast.deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(forecast_id),
    )
    await db.commit()


@router.get("/suggested", response_model=BulkSuggestedResponse)
async def list_suggested_forecasts(
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Weekly-bucketed, read-only demand suggestions for every item with
    consumption history, for review before POST /forecasts/suggested/accept."""
    if horizon_days <= 0 or horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")
    as_of = datetime.now(timezone.utc).date()
    suggestions = await compute_weekly_suggestions(
        db, current.org_id, horizon_days=horizon_days, as_of=as_of
    )
    return BulkSuggestedResponse(
        window_start=as_of + timedelta(days=1),
        window_end=as_of + timedelta(days=horizon_days),
        items=[_suggestion_to_response(s) for s in suggestions],
    )


MAX_ACCEPT_ITEMS = 500


@router.post("/suggested/accept", response_model=AcceptSuggestedResponse)
async def accept_suggested_forecasts(
    body: AcceptSuggestedRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Turn weekly suggestions into DemandForecast rows, server-authoritative:
    quantities are recomputed here, never taken from the client. For each item
    every existing row inside the suggestion window is deleted first. That is
    mandatory, not a convenience: as_of moves daily, so bucket boundaries
    shift, and plain upsert would leave yesterday's rows behind at the old
    boundaries, silently double-counting demand in MRP. Whole request is
    atomic, same as /forecasts/import."""
    if not body.item_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No item_ids provided")
    if len(body.item_ids) > MAX_ACCEPT_ITEMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"At most {MAX_ACCEPT_ITEMS} items per accept"
        )
    if body.horizon_days <= 0 or body.horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")

    parsed = list(dict.fromkeys(_parse_item_id(raw) for raw in body.item_ids))

    sku_by_id = {
        row.id: row.sku
        for row in (
            await db.execute(
                select(Item.id, Item.sku).where(Item.org_id == current.org_id, Item.id.in_(parsed))
            )
        ).all()
    }
    missing = set(parsed) - set(sku_by_id)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}",
        )

    as_of = datetime.now(timezone.utc).date()
    window_start = as_of + timedelta(days=1)
    window_end = as_of + timedelta(days=body.horizon_days)
    suggestions = {
        s.item_id: s
        for s in await compute_weekly_suggestions(
            db, current.org_id, horizon_days=body.horizon_days, as_of=as_of, item_ids=set(parsed)
        )
    }

    written = deleted = created = updated = 0
    skipped: list[SkippedSuggestion] = []
    for iid in parsed:
        sugg = suggestions.get(iid)
        if sugg is None:
            skipped.append(SkippedSuggestion(item_id=str(iid), item_sku=sku_by_id[iid]))
            continue
        result = await db.execute(
            delete(DemandForecast).where(
                DemandForecast.org_id == current.org_id,
                DemandForecast.item_id == iid,
                DemandForecast.period_start >= window_start,
                DemandForecast.period_start <= window_end,
            )
        )
        deleted += result.rowcount
        notes = f"from history ({sugg.method_used}, {sugg.history_points} days)"
        for p in sugg.points:
            _, was_created = await _upsert_forecast(
                db,
                org_id=current.org_id,
                item_id=iid,
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                notes=notes,
                source="suggested",
                created_by=current.id,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        written += 1

    await log_event(
        db,
        action="forecast.suggested_accepted",
        org_id=current.org_id,
        actor_user_id=current.id,
        metadata={
            "items": written,
            "deleted": deleted,
            "created": created,
            "updated": updated,
            "skipped": len(skipped),
            "horizon_days": body.horizon_days,
        },
    )
    await db.commit()
    return AcceptSuggestedResponse(
        items=written, deleted=deleted, created=created, updated=updated, skipped=skipped
    )


@router.get("/suggested/{item_id}", response_model=SuggestedForecastResponse)
async def get_suggested_forecast(
    item_id: uuid.UUID,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, data-driven demand projection for one item (moving average
    or exponential smoothing over its consumption history - see
    services.demand_forecasting), for review before turning it into an actual
    forecast via POST /forecasts. Never written anywhere itself."""
    if horizon_days <= 0 or horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")
    item = await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    result = await compute_demand_forecast(db, current.org_id, item_id, horizon_days=horizon_days)
    return SuggestedForecastResponse(
        item_id=str(item.id),
        item_sku=item.sku,
        item_name=item.name,
        method_used=result.method_used,
        history_points=result.history_points,
        points=[
            SuggestedForecastPoint(
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                lower=Decimal(str(round(p.lower, 4))) if p.lower is not None else None,
                upper=Decimal(str(round(p.upper, 4))) if p.upper is not None else None,
            )
            for p in result.points
        ],
    )
