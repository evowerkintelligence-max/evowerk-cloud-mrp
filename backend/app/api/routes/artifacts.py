from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.artifact import Artifact
from app.services import artifacts as artifacts_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    run_id: str | None
    report_id: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, a: Artifact) -> "ArtifactResponse":
        return cls(
            id=a.id,
            kind=a.kind,
            filename=a.filename,
            content_type=a.content_type,
            size_bytes=a.size_bytes,
            run_id=a.run_id,
            report_id=a.report_id,
            created_at=a.created_at,
        )


class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]
    total: int


async def _get_or_404(db: AsyncSession, org_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact:
    artifact = await artifacts_service.get_artifact(db, org_id, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    return artifact


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    kind: str | None = Query(None),
    run_id: str | None = Query(None),
    report_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ArtifactListResponse:
    items = await artifacts_service.list_artifacts(
        db, current.org_id, kind=kind, run_id=run_id, report_id=report_id, limit=limit, offset=offset
    )
    return ArtifactListResponse(items=[ArtifactResponse.from_model(a) for a in items], total=len(items))


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ArtifactResponse:
    artifact = await _get_or_404(db, current.org_id, artifact_id)
    return ArtifactResponse.from_model(artifact)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    artifact = await _get_or_404(db, current.org_id, artifact_id)
    path = artifacts_service.artifact_path(current.org_id, artifact.id, artifact.filename)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return Response(
        content=path.read_bytes(),
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    deleted = await artifacts_service.delete_artifact(db, current.org_id, artifact_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
