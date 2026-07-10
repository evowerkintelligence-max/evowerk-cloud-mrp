"""Dataset file storage.

Local-filesystem implementation. Files live at
``{data_dir}/datasets/{org_id}/{dataset_id}.{ext}`` — the path is derived
entirely from our own UUIDs, so a client-supplied filename can never influence
where a file lands (traversal-safe). Swapping in S3/object storage later means
replacing the bodies of these four functions, nothing above them.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import get_settings


def _root() -> Path:
    p = Path(get_settings().data_dir)
    return (p if p.is_absolute() else (Path.cwd() / p)).resolve()


def _org_dir(org_id: uuid.UUID) -> Path:
    d = _root() / "datasets" / str(org_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def dataset_path(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str) -> Path:
    return _org_dir(org_id) / f"{dataset_id}.{ext.lstrip('.')}"


def save_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str, data: bytes) -> Path:
    path = dataset_path(org_id, dataset_id, ext)
    path.write_bytes(data)
    return path


def delete_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str) -> None:
    try:
        dataset_path(org_id, dataset_id, ext).unlink()
    except FileNotFoundError:
        pass
