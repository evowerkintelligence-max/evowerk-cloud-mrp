from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.report_spec import SavedReport
from app.services import datasets as ds_service
from app.services.mrp import resolve_horizon
from app.services.rca import DIMENSIONS
from app.services.reports import (
    FORMATS,
    KINDS,
    NARRATION_STYLES,
    Report,
    ReportNarration,
    ReportSectionSpec,
    build_custom_report,
    build_dataset_report,
    build_inventory_analytics_report,
    build_mrp_plan_report,
    build_rca_report,
    build_scenario_report,
    get_saved_report_or_404,
    list_saved_reports,
    narrate_report,
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
    dataset_id: str | None,
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
    if kind == "dataset":
        if not dataset_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset reports need dataset_id")
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        return await build_dataset_report(db, org_id, dataset)
    # kind == "scenario" - validated by the caller against KINDS.
    return await build_scenario_report(db, org_id, await _parse_lead_times(db, org_id, lead_times))


class SavedReportWriteRequest(BaseModel):
    name: str
    sections: list[ReportSectionSpec] = []


class SavedReportResponse(BaseModel):
    id: str
    name: str
    sections: list[dict]
    created_at: str
    updated_at: str


def _saved_report_to_response(r: SavedReport) -> SavedReportResponse:
    return SavedReportResponse(
        id=str(r.id), name=r.name, sections=r.sections,
        created_at=r.created_at.isoformat(), updated_at=r.updated_at.isoformat(),
    )


# Registered ahead of GET /{kind} below - that catch-all would otherwise shadow
# the literal /reports/custom path (Starlette matches routes in add order).
@router.get("/custom", response_model=list[SavedReportResponse])
async def list_custom_reports(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await list_saved_reports(db, current.org_id)
    return [_saved_report_to_response(r) for r in rows]


@router.post("/custom", response_model=SavedReportResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_report(
    req: SavedReportWriteRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    report = SavedReport(
        org_id=current.org_id,
        name=req.name,
        sections=[s.model_dump(mode="json") for s in req.sections],
        created_by=current.id,
    )
    db.add(report)
    await log_event(
        db, action="report.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name}
    )
    await db.commit()
    await db.refresh(report)
    return _saved_report_to_response(report)


@router.get("/custom/{report_id}", response_model=SavedReportResponse)
async def get_custom_report(
    report_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    report = await get_saved_report_or_404(db, current.org_id, report_id)
    return _saved_report_to_response(report)


@router.put("/custom/{report_id}", response_model=SavedReportResponse)
async def update_custom_report(
    report_id: uuid.UUID,
    req: SavedReportWriteRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await get_saved_report_or_404(db, current.org_id, report_id)
    report.name = req.name
    report.sections = [s.model_dump(mode="json") for s in req.sections]
    await log_event(
        db, action="report.updated", org_id=current.org_id, actor_user_id=current.id, target=str(report.id)
    )
    await db.commit()
    await db.refresh(report)
    return _saved_report_to_response(report)


@router.delete("/custom/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_report(
    report_id: uuid.UUID, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
):
    report = await get_saved_report_or_404(db, current.org_id, report_id)
    await db.delete(report)
    await log_event(
        db, action="report.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(report_id)
    )
    await db.commit()


@router.get("/custom/{report_id}/render")
async def render_custom_report(
    report_id: uuid.UUID,
    format: str = Query("html", description="html | pdf | csv | xlsx"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"format must be one of {sorted(FORMATS)}")
    saved = await get_saved_report_or_404(db, current.org_id, report_id)
    sections = [ReportSectionSpec.model_validate(s) for s in saved.sections]
    report = await build_custom_report(db, current.org_id, saved.name, sections)
    disposition = "inline" if format in _INLINE_FORMATS else "attachment"
    return Response(
        content=render(report, format),
        media_type=FORMATS[format],
        headers={"Content-Disposition": f'{disposition}; filename="{report.filename(format)}"'},
    )


@router.post("/custom/{report_id}/narrate", response_model=ReportNarration)
async def narrate_custom_report(
    report_id: uuid.UUID,
    style: str = Query("narrative", description=" | ".join(NARRATION_STYLES)),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if style not in NARRATION_STYLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"style must be one of {NARRATION_STYLES}")
    saved = await get_saved_report_or_404(db, current.org_id, report_id)
    sections = [ReportSectionSpec.model_validate(s) for s in saved.sections]
    report = await build_custom_report(db, current.org_id, saved.name, sections)
    return await narrate_report(report, style)


@router.get("/{kind}")
async def get_report(
    kind: str,
    format: str = Query("html", description="html | pdf | csv | xlsx"),
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    dimension: str = "supplier",
    lead_times: str | None = Query(None, description="item_id:days,item_id:days (scenario only)"),
    dataset_id: str | None = Query(None, description="dataset id (dataset kind only)"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render one of the report kinds as a standalone HTML, PDF or CSV
    document. Four kinds are built fresh from this org's live data; "dataset"
    is built from a chosen uploaded dataset instead. Nothing is stored: the
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
        dataset_id=dataset_id,
    )
    disposition = "inline" if format in _INLINE_FORMATS else "attachment"
    return Response(
        content=render(report, format),
        media_type=FORMATS[format],
        headers={"Content-Disposition": f'{disposition}; filename="{report.filename(format)}"'},
    )


@router.post("/{kind}/narrate", response_model=ReportNarration)
async def narrate_report_kind(
    kind: str,
    style: str = Query("narrative", description=" | ".join(NARRATION_STYLES)),
    horizon_start: date | None = None,
    horizon_end: date | None = None,
    dimension: str = "supplier",
    lead_times: str | None = Query(None, description="item_id:days,item_id:days (scenario only)"),
    dataset_id: str | None = Query(None, description="dataset id (dataset kind only)"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if kind not in KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown report: {kind}")
    if style not in NARRATION_STYLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"style must be one of {NARRATION_STYLES}")

    report = await _build(
        db,
        current.org_id,
        kind,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        dimension=dimension,
        lead_times=lead_times,
        dataset_id=dataset_id,
    )
    return await narrate_report(report, style)
