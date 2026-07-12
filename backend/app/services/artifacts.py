"""Generated-file storage (exports, report renders, run outputs).

Local-filesystem implementation, same shape as app.services.storage for
datasets: files live at ``{data_dir}/artifacts/{org_id}/{artifact_id}.{ext}``,
the path is derived entirely from our own UUIDs (traversal-safe), and the row
in the `artifacts` table is the source of truth for everything else.
"""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.artifact import Artifact


def _root() -> Path:
    p = Path(get_settings().data_dir)
    return (p if p.is_absolute() else (Path.cwd() / p)).resolve()


def _org_dir(org_id: uuid.UUID) -> Path:
    d = _root() / "artifacts" / str(org_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_path(org_id: uuid.UUID, artifact_id: uuid.UUID, filename: str) -> Path:
    ext = Path(filename).suffix or ""
    return _org_dir(org_id) / f"{artifact_id}{ext}"


async def create_artifact(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    kind: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    run_id: str | None = None,
    report_id: str | None = None,
) -> Artifact:
    artifact = Artifact(
        org_id=org_id,
        kind=kind,
        filename=filename,
        content_type=content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=len(data),
        run_id=run_id,
        report_id=report_id,
        created_by=created_by,
    )
    db.add(artifact)
    await db.flush()  # assigns artifact.id before it's used in the file path
    artifact_path(org_id, artifact.id, filename).write_bytes(data)
    await db.commit()
    await db.refresh(artifact)
    return artifact


async def list_artifacts(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    kind: str | None = None,
    run_id: str | None = None,
    report_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Artifact]:
    stmt = select(Artifact).where(Artifact.org_id == org_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if run_id:
        stmt = stmt.where(Artifact.run_id == run_id)
    if report_id:
        stmt = stmt.where(Artifact.report_id == report_id)
    stmt = stmt.order_by(Artifact.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_artifact(db: AsyncSession, org_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact | None:
    result = await db.execute(
        select(Artifact).where(Artifact.org_id == org_id, Artifact.id == artifact_id)
    )
    return result.scalar_one_or_none()


async def delete_artifact(db: AsyncSession, org_id: uuid.UUID, artifact_id: uuid.UUID) -> bool:
    artifact = await get_artifact(db, org_id, artifact_id)
    if artifact is None:
        return False
    path = artifact_path(org_id, artifact.id, artifact.filename)
    await db.delete(artifact)
    await db.commit()
    path.unlink(missing_ok=True)
    return True
