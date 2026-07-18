from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.report_spec import ReportInboxItem, SavedReport
from app.services.reports import (
    FORMATS,
    NARRATION_STYLES,
    ReportNarration,
    ReportSectionSpec,
    build_custom_report,
    get_saved_report_or_404,
    list_saved_reports,
    narrate_report,
    render,
)

router = APIRouter(prefix="/reports", tags=["reports"])

# HTML is a document to look at; CSV and PDF are files to keep.
_INLINE_FORMATS = ("html",)


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


class InboxItemCreateRequest(ReportSectionSpec):
    label: str


class InboxItemResponse(BaseModel):
    id: str
    label: str
    spec: dict
    created_by: str
    created_at: str


def _inbox_item_to_response(item: ReportInboxItem) -> InboxItemResponse:
    return InboxItemResponse(
        id=str(item.id), label=item.label, spec=item.spec,
        created_by=str(item.created_by), created_at=item.created_at.isoformat(),
    )


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
    report = await build_custom_report(db, current, saved.name, sections)
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
    report = await build_custom_report(db, current, saved.name, sections)
    return await narrate_report(report, style)


@router.post("/inbox", response_model=InboxItemResponse, status_code=status.HTTP_201_CREATED)
async def create_inbox_item(
    req: InboxItemCreateRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    spec = ReportSectionSpec(**req.model_dump(exclude={"label"}))
    item = ReportInboxItem(
        org_id=current.org_id, created_by=current.id, label=req.label,
        spec=spec.model_dump(mode="json"),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _inbox_item_to_response(item)


@router.get("/inbox", response_model=list[InboxItemResponse])
async def list_inbox_items(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ReportInboxItem)
            .where(ReportInboxItem.org_id == current.org_id)
            .order_by(ReportInboxItem.created_at.desc())
        )
    ).scalars().all()
    return [_inbox_item_to_response(r) for r in rows]


@router.delete("/inbox/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox_item(
    item_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = (
        await db.execute(
            select(ReportInboxItem).where(ReportInboxItem.id == item_id, ReportInboxItem.org_id == current.org_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbox item not found")
    if item.created_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the item's creator or an owner can delete it")
    await db.delete(item)
    await log_event(
        db, action="report_inbox.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(item_id)
    )
    await db.commit()
