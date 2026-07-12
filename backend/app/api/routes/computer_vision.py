from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.services import computer_vision as service

router = APIRouter(prefix="/computer-vision", tags=["computer-vision"])

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class CvFindingResponse(BaseModel):
    label: str
    confidence: str
    location: str | None
    severity: str | None


class CvResponse(BaseModel):
    task: str
    mode: str
    summary: str
    findings: list[CvFindingResponse]
    compliance_pct: float | None
    count: int | None


@router.post("/analyze", response_model=CvResponse)
async def analyze(
    task: str = Form(default="defect_detection"),
    image: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
) -> CvResponse:
    content_type = image.content_type or "image/jpeg"
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported image type: {content_type}")

    image_bytes = await image.read()
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image exceeds 20 MB limit")

    try:
        result = await service.analyze(task, image_bytes, content_type)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return CvResponse(
        task=result.task, mode=result.mode, summary=result.summary,
        findings=[CvFindingResponse(**vars(f)) for f in result.findings],
        compliance_pct=result.compliance_pct, count=result.count,
    )
