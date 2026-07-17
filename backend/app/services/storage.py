"""Dataset file storage.

Local-filesystem implementation. Files live at
``{data_dir}/datasets/{org_id}/{dataset_id}.{ext}`` — the path is derived
entirely from our own UUIDs, so a client-supplied filename can never influence
where a file lands (traversal-safe). Swapping in S3/object storage later means
replacing the bodies of these five functions, nothing above them.
"""
from __future__ import annotations

import os
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


def replace_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str, data: bytes) -> Path:
    """Rewrite an existing dataset file atomically (temp file + os.replace).

    Unlike save_dataset_file (used exactly once, before the Dataset row
    exists and before any reader could be mid-request against it), this is
    called repeatedly against a file that may be read concurrently by the
    analytics engines - a plain write_bytes could hand a reader a
    half-written CSV. os.replace is atomic on the same filesystem on both
    POSIX and Windows.
    """
    path = dataset_path(org_id, dataset_id, ext)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def delete_dataset_file(org_id: uuid.UUID, dataset_id: uuid.UUID, ext: str) -> None:
    try:
        dataset_path(org_id, dataset_id, ext).unlink()
    except FileNotFoundError:
        pass
