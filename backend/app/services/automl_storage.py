"""AutoML run artifact storage.

Local-filesystem implementation, same shape as app/services/storage.py: paths
are derived entirely from our own UUIDs (traversal-safe). Files live at
``{data_dir}/automl_runs/{org_id}/{run_id}/*``.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings


def _root() -> Path:
    p = Path(get_settings().data_dir)
    return (p if p.is_absolute() else (Path.cwd() / p)).resolve()


def run_dir(org_id: uuid.UUID, run_id: uuid.UUID) -> Path:
    d = _root() / "automl_runs" / str(org_id) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def delete_run_dir(org_id: uuid.UUID, run_id: uuid.UUID) -> None:
    shutil.rmtree(_root() / "automl_runs" / str(org_id) / str(run_id), ignore_errors=True)
