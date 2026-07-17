"""AutoML run artifact storage.

Local-filesystem implementation, same shape as app/services/storage.py: paths
are derived entirely from our own UUIDs (traversal-safe). Files live at
``{data_dir}/automl_runs/{org_id}/{run_id}/*``.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pandas as pd

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


def load_explain_sample(org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[pd.DataFrame | None, pd.Series | None]:
    """The (X, y) sample saved at training time for explainability/drift -
    see automl_runner.execute_automl_run. Returns (None, None) if missing."""
    d = run_dir(org_id, run_id)
    x_path, y_path = d / "explain_X.csv", d / "explain_y.csv"
    if not x_path.exists() or not y_path.exists():
        return None, None
    X = pd.read_csv(x_path)
    ydf = pd.read_csv(y_path)
    y = ydf["y"] if "y" in ydf.columns else ydf.iloc[:, 0]
    return X, y
