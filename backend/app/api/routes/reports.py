from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.bom import Item
from app.services.mrp import resolve_horizon
from app.services.rca import DIMENSIONS
from app.services.reports import (
    FORMATS,
    KINDS,
    Report,
    build_inventory_analytics_report,
    build_mrp_plan_report,
    build_rca_report,
    build_scenario_report,
    render,
)

router = APIRouter(prefix="/reports", tags=["reports"])

# HTML is a document to look at; CSV and PDF are files to keep.
_INLINE_FORMATS = ("html",)


async def _parse_lead_times(
    db: AsyncSession, org_id: uuid.UUID, raw: str | None
) -> dict[uuid.UUID, int]:
    """`lead_times=<item_id>:<days>,<item_id>:<days>` - the scenario report's
    overrides live in the query string so a report is one GET and downloads
    from a plain link, like every other kind."""
    if not raw or not raw.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "scenario reports need lead_times=<item_id>:<days>[,<item_id>:<days>]",
        )

    overrides: dict[uuid.UUID, int] = {}
    for pair in raw.split(","):
        item_raw, sep, days_raw = pair.strip().partition(":")
        if not sep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Malformed lead_times entry: {pair}")
        try:
            item_id = uuid.UUID(item_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {item_raw}") from None
        try:
            days = int(days_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid lead time: {days_raw}") from None
        if days < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"lead_time_days must be non-negative (item {item_raw})"
            )
        overrides[item_id] = days

    found = (
        await db.execute(select(Item.id).where(Item.org_id == org_id, Item.id.in_(overrides.keys())))
    ).scalars().all()
    missing = overrides.keys() - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )
    return overrides


async def _build(
    db: AsyncSession,
    org_id: uuid.UUID,
    kind: str,
    *,
    horizon_start: date | None,
    horizon_end: date | None,
    dimension: str,
    lead_times: str | None,
) -> Report:
    if kind == "mrp-plan":
        hs, he = resolve_horizon(horizon_start, horizon_end)
        return await build_mrp_plan_report(db, org_id, hs, he)
    if kind == "inventory-analytics":
        return await build_inventory_analytics_report(db, org_id)
    if kind == "rca":
        if dimension not in DIMENSIONS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"dimension must be one of {sorted(DIMENSIONS)}"
            )
        return await build_rca_report(db, org_id, dimension)
    # kind == "scenario" - validated by the caller against KINDS.
    return await build_scenario_report(db, org_id, await _parse_lead_times(db, org_id, lead_times))


@router.get("/{kind}")
async def get_report(
    kind: str,
    format: str = Query("html", description="html | pdf | csv"),
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    dimension: str = "supplier",
    lead_times: str | None = Query(None, description="item_id:days,item_id:days (scenario only)"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render one of the four report kinds as a standalone HTML, PDF or CSV
    document, built fresh from this org's live data. Nothing is stored: the
    response body *is* the report - see app.services.reports."""
    if kind not in KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown report: {kind}")
    if format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"format must be one of {sorted(FORMATS)}")

    report = await _build(
        db,
        current.org_id,
        kind,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        dimension=dimension,
        lead_times=lead_times,
    )
    disposition = "inline" if format in _INLINE_FORMATS else "attachment"
    return Response(
        content=render(report, format),
        media_type=FORMATS[format],
        headers={"Content-Disposition": f'{disposition}; filename="{report.filename(format)}"'},
    )
