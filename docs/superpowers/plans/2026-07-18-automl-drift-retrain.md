# Automated Drift-Triggered Retraining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a live-refreshed Dataset an opted-in AutoML run was trained on gets new data, automatically check drift and retrain — with a quality gate (don't promote a worse model) and a circuit breaker (stop after 3 consecutive non-improving retrains).

**Architecture:** Event-driven off the existing `datasets.overwrite_dataset_file` call (the only "this dataset's content just changed" moment in the codebase), via FastAPI `BackgroundTasks` — no new scheduler. A new `automl_retrain.py` service owns the drift-check → retrain → quality-gate → circuit-breaker decision logic in one place; two small refactors (relocating a helper, renaming another to be public) unblock reuse; three new columns on `AutoMLRun` track opt-in, lineage, and the circuit-breaker streak.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + DuckDB (backend), React + TanStack Query (frontend). Same libraries already used throughout `automl_engine.py`/`automl_runner.py` — no new dependencies.

**Full design context:** `docs/superpowers/specs/2026-07-18-automl-drift-retrain-design.md`

## Global Constraints

- Every new/modified service function follows this codebase's established pattern: services `flush()`/business-logic only, routes `commit()` — except `automl_retrain.py`, which owns its own DB session (opened via `db_session.async_session_factory()`, mirroring `automl_runner.execute_automl_run` exactly) since it runs as a `BackgroundTasks` callback after the triggering request has already returned, and therefore commits its own work directly (there is no route left to commit for it).
- No new third-party dependencies. No changes to `automl_engine.py`'s actual training/scoring behavior — only a rename of an existing pure function.
- New/changed model columns must be registered the same way existing `AutoMLRun` columns are (no new model-import needed in `backend/tests/conftest.py` — `automl_run` is already registered there).
- `pragma: allowlist secret` stays on the migration's `revision`/`down_revision` literals.
- Frontend has no automated test runner — frontend tasks end with `tsc -b` typecheck + a manual in-browser verification pass.
- The circuit breaker trips **immediately**, in the same evaluation that produces the 3rd consecutive non-promoted retrain — not on a later, separate check. See Task 3.

---

### Task 1: Data model + migration

**Files:**
- Modify: `backend/app/models/automl_run.py`
- Create: `backend/alembic/versions/<new_revision>_automl_retrain_columns.py`
- Test: `backend/tests/test_automl_retrain.py` (new file)

**Interfaces:**
- Consumes: existing `app.models.automl_run.AutoMLRun`.
- Produces: three new columns — `auto_retrain: bool` (default `False`), `retrained_from_run_id: uuid.UUID | None` (self-referential FK, `ondelete="SET NULL"`), `auto_retrain_streak: int` (default `0`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_automl_retrain.py`:

```python
# backend/tests/test_automl_retrain.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "ar@example.com", org_name: str = "ArOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_automl_run_retrain_columns_roundtrip(client, test_engine):
    from app.models.automl_run import AutoMLRun
    from app.models.dataset import Dataset

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = Dataset(
            org_id=org_id, name="D", original_filename="d.csv", fmt="csv", storage_ext="csv",
            columns=[{"name": "x", "dtype": "VARCHAR"}], row_count=1, size_bytes=1, created_by=user_id,
        )
        db.add(dataset)
        await db.flush()

        parent = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Parent",
            task="regression", target_column="x", status="success", auto_retrain=True,
        )
        db.add(parent)
        await db.flush()

        child = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Child",
            task="regression", target_column="x", status="success",
            auto_retrain=True, retrained_from_run_id=parent.id, auto_retrain_streak=0,
        )
        db.add(child)
        await db.commit()
        await db.refresh(child)

        assert child.retrained_from_run_id == parent.id
        assert child.auto_retrain is True
        assert child.auto_retrain_streak == 0

        default_run = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Default",
            task="regression", target_column="x", status="queued",
        )
        db.add(default_run)
        await db.commit()
        await db.refresh(default_run)
        assert default_run.auto_retrain is False
        assert default_run.retrained_from_run_id is None
        assert default_run.auto_retrain_streak == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py -v`
Expected: FAIL with `TypeError: 'auto_retrain' is an invalid keyword argument for AutoMLRun` (or similar — the columns don't exist yet).

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/automl_run.py`, find:

```python
from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
```

Replace with:

```python
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
```

Then find:

```python
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

Replace with:

```python
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Opt-in, settable only at creation - see app.services.automl_retrain.
    # True means "this run is the currently-watched one for its dataset."
    auto_retrain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set on a run created by automl_retrain.maybe_retrain_for_dataset.
    retrained_from_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automl_runs.id", ondelete="SET NULL"), nullable=True
    )
    # Consecutive auto-retrain attempts for this lineage that did NOT pass
    # the quality gate; reset to 0 on a promoted retrain. The circuit
    # breaker trips (auto_retrain -> False) once this reaches
    # automl_retrain.MAX_AUTO_RETRAIN_STREAK.
    auto_retrain_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py -v`
Expected: PASS.

- [ ] **Step 5: Create the Alembic migration**

Run `cd backend && python -m alembic heads` and confirm the single head. If it is not `f1a5c8d3e942` (e.g. because the shift-forecast branch's `b6d3f8a17c92` migration has landed on `master` since this plan was written), use the real current head as `down_revision` below instead.

Create `backend/alembic/versions/<new_revision>_automl_retrain_columns.py` (pick a fresh, unique 12-hex-char revision id not already used in `backend/alembic/versions/`):

```python
"""automl run retrain columns

Revision ID: <new_revision>
Revises: f1a5c8d3e942
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<new_revision>"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automl_runs",
        sa.Column("auto_retrain", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("automl_runs", sa.Column("retrained_from_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_automl_runs_retrained_from_run_id", "automl_runs", "automl_runs",
        ["retrained_from_run_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "automl_runs",
        sa.Column("auto_retrain_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_constraint("fk_automl_runs_retrained_from_run_id", "automl_runs", type_="foreignkey")
    op.drop_column("automl_runs", "auto_retrain_streak")
    op.drop_column("automl_runs", "retrained_from_run_id")
    op.drop_column("automl_runs", "auto_retrain")
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/automl_run.py backend/alembic/versions/*_automl_retrain_columns.py backend/tests/test_automl_retrain.py
git commit -m "feat: add auto-retrain columns to AutoMLRun"
```

---

### Task 2: Backend refactors — relocate/rename helpers, extract run-creation

**Files:**
- Modify: `backend/app/services/automl_storage.py`
- Modify: `backend/app/services/automl_engine.py`
- Modify: `backend/app/services/automl_runner.py`
- Modify: `backend/app/api/routes/automl.py`
- Test: `backend/tests/test_automl.py` (append)

**Interfaces:**
- Consumes: existing `app.models.automl_run.AutoMLRun` (Task 1's new columns).
- Produces:
  - `app.services.automl_storage.load_explain_sample(org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[pd.DataFrame | None, pd.Series | None]` (moved from `routes/automl.py`'s private `_load_explain_sample`).
  - `app.services.automl_engine.normalize_higher_is_better(task: AutoMLTask, metric_value: float) -> float` (renamed from `_normalize_higher_is_better`, same behavior).
  - `app.services.automl_runner.create_run(db, *, org_id, created_by, dataset_id, name, task, target_column, feature_columns, config, auto_retrain=False, retrained_from_run_id=None) -> AutoMLRun` (async; builds + `db.add` + `db.flush()`s an `AutoMLRun` row — does not commit, does not schedule training; the caller does both).

This task is a behavior-preserving refactor plus wiring `auto_retrain` through the existing create-run request/response — no drift/retrain logic yet (that's Task 3).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_automl.py`:

```python
@pytest.mark.asyncio
async def test_create_run_accepts_auto_retrain_flag(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    body = {
        "name": "Predict y", "dataset_id": dataset_id, "task": "classification",
        "target_column": "y", "feature_columns": ["x1", "x2"], "thoroughness": "fast",
        "auto_retrain": True,
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    assert created.json()["auto_retrain"] is True
    assert created.json()["retrained_from_run_id"] is None

    detail = await client.get(f"/automl/runs/{created.json()['id']}", headers=headers)
    assert detail.json()["auto_retrain"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl.py::test_create_run_accepts_auto_retrain_flag -v`
Expected: FAIL — `auto_retrain` is not accepted/returned yet (either a 422 on the request body being rejected as an unknown field is fine either way since Pydantic ignores unknown fields by default, so this most likely fails on `created.json()["auto_retrain"]` raising `KeyError`).

- [ ] **Step 3: Move `_load_explain_sample` into `automl_storage.py`**

In `backend/app/services/automl_storage.py`, find:

```python
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
```

Replace with:

```python
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
```

Then find:

```python
def delete_run_dir(org_id: uuid.UUID, run_id: uuid.UUID) -> None:
    shutil.rmtree(_root() / "automl_runs" / str(org_id) / str(run_id), ignore_errors=True)
```

Replace with:

```python
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
```

- [ ] **Step 4: Rename `_normalize_higher_is_better` to `normalize_higher_is_better`**

In `backend/app/services/automl_engine.py`, there are 3 occurrences of `_normalize_higher_is_better` (the definition and 2 call sites, all within this same file). Replace all 3 with `normalize_higher_is_better` (drop the leading underscore) — the function body and every other line stays identical:

```python
def normalize_higher_is_better(task: AutoMLTask, metric_value: float) -> float:
    """Flips lower-is-better metrics (rmse/mae) so every selection score compares as 'higher wins'."""
    return metric_value if task == AutoMLTask.classification else -metric_value
```

- [ ] **Step 5: Add `automl_runner.create_run`**

In `backend/app/services/automl_runner.py`, `AsyncSession` is already imported at the top of the file (`from sqlalchemy.ext.asyncio import AsyncSession`, used by `_execute`) — `create_run` below uses that same import, no new import needed. Find:

```python
from app.services.datasets import dataframe_for


async def execute_automl_run(run_id: str, org_id: str) -> None:
```

Replace with:

```python
from app.services.datasets import dataframe_for


async def create_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    dataset_id: uuid.UUID,
    name: str,
    task: str,
    target_column: str,
    feature_columns: list[str],
    config: dict,
    auto_retrain: bool = False,
    retrained_from_run_id: uuid.UUID | None = None,
) -> AutoMLRun:
    """Build + persist a queued AutoMLRun row. Does not commit (the caller
    does - a route commits once; app.services.automl_retrain commits its own
    background-task session) and does not schedule training (the caller
    decides how: a route uses BackgroundTasks.add_task(execute_automl_run,
    ...); automl_retrain, which has no BackgroundTasks to schedule onto
    since its triggering request already returned, awaits it directly)."""
    run = AutoMLRun(
        org_id=org_id, dataset_id=dataset_id, created_by=created_by, name=name, task=task,
        target_column=target_column, feature_columns=feature_columns, config=config,
        auto_retrain=auto_retrain, retrained_from_run_id=retrained_from_run_id, status="queued",
    )
    db.add(run)
    await db.flush()
    return run


async def execute_automl_run(run_id: str, org_id: str) -> None:
```

- [ ] **Step 6: Wire `auto_retrain` through the route and refactor `create_run` to use the new helper**

In `backend/app/api/routes/automl.py`, find:

```python
class TrainRequest(BaseModel):
    name: str
    dataset_id: uuid.UUID
    task: str
    target_column: str
    feature_columns: list[str] = []
    thoroughness: str = "balanced"
    algorithms: list[str] | None = None
    handle_class_imbalance: bool = True
    time_column: str | None = None
    horizon: int = 1
    forecast_strategy: str = "direct"
    time_budget_seconds: int | None = None
    search_method: str = "random"
```

Replace with:

```python
class TrainRequest(BaseModel):
    name: str
    dataset_id: uuid.UUID
    task: str
    target_column: str
    feature_columns: list[str] = []
    thoroughness: str = "balanced"
    algorithms: list[str] | None = None
    handle_class_imbalance: bool = True
    time_column: str | None = None
    horizon: int = 1
    forecast_strategy: str = "direct"
    time_budget_seconds: int | None = None
    search_method: str = "random"
    auto_retrain: bool = False
```

Then find:

```python
class RunSummary(BaseModel):
    id: str
    name: str
    task: str
    status: str
    dataset_id: str
    target_column: str
    best_model_name: str | None
    metric_name: str | None
    metric_value: float | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
```

Replace with:

```python
class RunSummary(BaseModel):
    id: str
    name: str
    task: str
    status: str
    dataset_id: str
    target_column: str
    best_model_name: str | None
    metric_name: str | None
    metric_value: float | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    auto_retrain: bool
    retrained_from_run_id: str | None
```

Then find:

```python
def _summary(r: AutoMLRun) -> RunSummary:
    return RunSummary(
        id=str(r.id),
        name=r.name,
        task=r.task,
        status=r.status,
        dataset_id=str(r.dataset_id),
        target_column=r.target_column,
        best_model_name=r.best_model_name,
        metric_name=r.metric_name,
        metric_value=r.metric_value,
        error=r.error,
        created_at=r.created_at.isoformat(),
        started_at=r.started_at.isoformat() if r.started_at else None,
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
    )
```

Replace with:

```python
def _summary(r: AutoMLRun) -> RunSummary:
    return RunSummary(
        id=str(r.id),
        name=r.name,
        task=r.task,
        status=r.status,
        dataset_id=str(r.dataset_id),
        target_column=r.target_column,
        best_model_name=r.best_model_name,
        metric_name=r.metric_name,
        metric_value=r.metric_value,
        error=r.error,
        created_at=r.created_at.isoformat(),
        started_at=r.started_at.isoformat() if r.started_at else None,
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        auto_retrain=r.auto_retrain,
        retrained_from_run_id=str(r.retrained_from_run_id) if r.retrained_from_run_id else None,
    )
```

Then find:

```python
def _load_explain_sample(org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[pd.DataFrame | None, pd.Series | None]:
    run_dir = automl_storage.run_dir(org_id, run_id)
    x_path, y_path = run_dir / "explain_X.csv", run_dir / "explain_y.csv"
    if not x_path.exists() or not y_path.exists():
        return None, None
    X = pd.read_csv(x_path)
    ydf = pd.read_csv(y_path)
    y = ydf["y"] if "y" in ydf.columns else ydf.iloc[:, 0]
    return X, y
```

Delete this whole function (it's moved to `automl_storage.load_explain_sample` — Step 3 above). Then, every remaining call site in this same file that reads `_load_explain_sample(...)` (there are 5 — search for the literal text `_load_explain_sample(current.org_id, run_id)`) becomes `automl_storage.load_explain_sample(current.org_id, run_id)`. `automl_storage` is already imported at the top of this file.

Then find:

```python
    run = AutoMLRun(
        org_id=current.org_id,
        dataset_id=dataset.id,
        created_by=current.id,
        name=req.name,
        task=req.task,
        target_column=req.target_column,
        feature_columns=req.feature_columns,
        config={
            "thoroughness": req.thoroughness,
            "algorithms": req.algorithms,
            "handle_class_imbalance": req.handle_class_imbalance,
            "time_column": req.time_column,
            "horizon": req.horizon,
            "forecast_strategy": req.forecast_strategy,
            "time_budget_seconds": req.time_budget_seconds,
            "search_method": req.search_method,
        },
        status="queued",
    )
    db.add(run)
    await log_event(db, action="automl.train", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name, "task": req.task})
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_automl_run, str(run.id), str(current.org_id))
    return _detail(run)
```

Replace with:

```python
    run = await automl_runner.create_run(
        db,
        org_id=current.org_id,
        created_by=current.id,
        dataset_id=dataset.id,
        name=req.name,
        task=req.task,
        target_column=req.target_column,
        feature_columns=req.feature_columns,
        config={
            "thoroughness": req.thoroughness,
            "algorithms": req.algorithms,
            "handle_class_imbalance": req.handle_class_imbalance,
            "time_column": req.time_column,
            "horizon": req.horizon,
            "forecast_strategy": req.forecast_strategy,
            "time_budget_seconds": req.time_budget_seconds,
            "search_method": req.search_method,
        },
        auto_retrain=req.auto_retrain,
    )
    await log_event(db, action="automl.train", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name, "task": req.task})
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_automl_run, str(run.id), str(current.org_id))
    return _detail(run)
```

Finally, find the import block at the top of this file:

```python
from app.services import automl_engine, automl_storage, datasets as datasets_service
from app.services.automl_runner import execute_automl_run
```

Replace with:

```python
from app.services import automl_engine, automl_runner, automl_storage, datasets as datasets_service
from app.services.automl_runner import execute_automl_run
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_automl.py -v`
Expected: all PASS, including the new `test_create_run_accepts_auto_retrain_flag`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/automl_storage.py backend/app/services/automl_engine.py backend/app/services/automl_runner.py backend/app/api/routes/automl.py backend/tests/test_automl.py
git commit -m "refactor: relocate explain-sample loader, publicize metric normalizer, extract run-creation helper"
```

---

### Task 3: `automl_retrain.py` — drift check, quality gate, circuit breaker

**Files:**
- Create: `backend/app/services/automl_retrain.py`
- Test: `backend/tests/test_automl_retrain.py` (append)

**Interfaces:**
- Consumes: `app.services.automl_storage.load_explain_sample`, `app.services.automl_engine.check_data_drift` / `normalize_higher_is_better` / `AutoMLTask` (all existing/Task 2), `app.services.automl_runner.create_run` / `execute_automl_run` (Task 2 / existing), `app.services.datasets.dataframe_for` (existing), `app.middleware.audit.log_event` (existing), `app.db.session.async_session_factory` (existing, same one `execute_automl_run` uses).
- Produces: `app.services.automl_retrain.maybe_retrain_for_dataset(org_id: str, dataset_id: str) -> None` (async — this exact signature, string args, is what Task 4 schedules via `BackgroundTasks.add_task`, mirroring `execute_automl_run(run_id: str, org_id: str)`'s string-args convention). Also exports `MAX_AUTO_RETRAIN_STREAK = 3` and `DRIFT_SEVERITY_THRESHOLD = "high"` as module constants (referenced by tests).

This task is **not yet wired to any trigger** — it's tested by calling `maybe_retrain_for_dataset` directly against seeded data. Task 4 wires the actual trigger.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_automl_retrain.py`:

```python
import asyncio
import io

import pandas as pd
from sqlalchemy import select


def _classification_csv(n: int, *, offset: float = 0.0) -> bytes:
    """x1,x2,y where y = 1 if x1+x2>0 else 0. check_data_drift computes PSI
    on the FEATURE columns (x1/x2) against the run's training-time sample,
    not on y - so offset shifts x1/x2's whole range, which is what actually
    needs to change between calls to simulate genuine feature drift.
    offset=0 (the default) reproduces the same distribution used for
    training; a large offset (e.g. 20) shifts it enough to register as PSI
    "high"."""
    rows = ["x1,x2,y"]
    for i in range(n):
        x1 = (i % 7) - 3 + offset
        x2 = ((i * 3) % 5) - 2 + offset
        y = 1 if x1 + x2 > 0 else 0
        rows.append(f"{x1},{x2},{y}")
    return ("\n".join(rows) + "\n").encode()


async def _train_success_run(client, headers, dataset_id, *, auto_retrain=True):
    body = {
        "name": "Predict y", "dataset_id": dataset_id, "task": "classification",
        "target_column": "y", "feature_columns": ["x1", "x2"], "thoroughness": "fast",
        "auto_retrain": auto_retrain,
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    run_id = created.json()["id"]
    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.json()["status"] == "success", detail.json().get("error")
    return run_id


async def test_no_watched_runs_is_a_noop(client, test_engine):
    from app.services import automl_retrain

    org_id, _ = await _register(client)
    # No AutoMLRun rows at all for this org/dataset - must not raise.
    await automl_retrain.maybe_retrain_for_dataset(str(org_id), str(uuid.uuid4()))


async def test_low_drift_does_not_retrain(client, test_engine):
    from app.services import automl_retrain
    from app.services import datasets as ds_service
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "ardrift1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_local(client, headers, _classification_csv(60))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Overwrite the SAME dataset with a same-distribution, larger sample
    # (same generation rule, no label flip) - low/no drift expected.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(65))
        await db.commit()

    await automl_retrain.maybe_retrain_for_dataset(str(org_id), dataset_id)

    runs = await client.get("/automl/runs", headers=headers)
    # Still just the one run - no retrain fired for same-distribution data.
    assert len(runs.json()) == 1
    assert runs.json()[0]["id"] == run_id


async def test_high_drift_triggers_promoted_retrain(client, test_engine):
    from app.services import automl_retrain
    from app.models.automl_run import AutoMLRun
    from app.services import datasets as ds_service
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "ardrift2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_local(client, headers, _classification_csv(80))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Overwrite with the same rule but x1/x2 shifted well outside the
    # training-time range - large PSI shift on the feature columns
    # check_data_drift actually measures.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(80, offset=20))
        await db.commit()

    await automl_retrain.maybe_retrain_for_dataset(str(org_id), dataset_id)

    runs = (await client.get("/automl/runs", headers=headers)).json()
    assert len(runs) == 2
    child = next(r for r in runs if r["id"] != run_id)
    parent = next(r for r in runs if r["id"] == run_id)
    assert child["retrained_from_run_id"] == run_id

    # Promoted or rejected depends on whether the shifted-range retrain
    # scores at least as well as the original - both are valid outcomes of
    # this synthetic setup, so assert the invariant that actually matters:
    # exactly one of {parent, child} is the currently-watched run, never
    # both/neither.
    assert (parent["auto_retrain"], child["auto_retrain"]).count(True) == 1


async def test_circuit_breaker_trips_after_max_consecutive_rejections(client, test_engine, monkeypatch):
    from app.services import automl_retrain
    from app.models.automl_run import AutoMLRun
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "arbreaker@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_local(client, headers, _classification_csv(80))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Force every retrain attempt to score worse than its parent, regardless
    # of what actually trained, so the circuit breaker is exercised
    # deterministically rather than depending on real model variance.
    # _evaluate_run calls normalize_higher_is_better exactly twice per
    # evaluation, in order: once for the parent's score, once for the
    # child's - so alternating high/low values across calls makes every
    # single evaluation resolve as "child scored worse."
    call_values = iter([10.0, -10.0] * automl_retrain.MAX_AUTO_RETRAIN_STREAK)
    monkeypatch.setattr(
        automl_retrain.automl_engine, "normalize_higher_is_better",
        lambda task, value: next(call_values),
    )

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    for i in range(automl_retrain.MAX_AUTO_RETRAIN_STREAK):
        async with session_factory() as db:
            from app.services import datasets as ds_service
            dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
            await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(80, offset=20))
            await db.commit()
        # Bypass the 1-hour cooldown between attempts within this test by
        # calling the internal evaluator directly against a fresh session,
        # rather than sleeping in a test.
        async with session_factory() as db:
            run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == uuid.UUID(run_id)))).scalar_one()
            dataset = (await db.execute(select(automl_retrain.Dataset).where(automl_retrain.Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
            await automl_retrain._evaluate_run(db, run, dataset, skip_cooldown=True)

    async with session_factory() as db:
        run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == uuid.UUID(run_id)))).scalar_one()
        assert run.auto_retrain is False
        assert run.auto_retrain_streak == automl_retrain.MAX_AUTO_RETRAIN_STREAK
```

The tests above use two small local helpers not yet defined in this file — add them near the top of `backend/tests/test_automl_retrain.py` (below the existing `_register` helper), matching the `_register_and_login`/`_upload` pattern already used in `test_downtime_tracking.py` and `test_automl.py`:

```python
async def _register_and_login_local(client, email: str, org_name: str = "ArOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_local(client, headers, content: bytes) -> str:
    res = await client.post("/datasets", headers=headers, files={"file": ("d.csv", content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_retrain'`.

- [ ] **Step 3: Create `automl_retrain.py`**

Create `backend/app/services/automl_retrain.py`:

```python
# backend/app/services/automl_retrain.py
"""Event-driven drift check + retrain. app.services.datasets.
overwrite_dataset_file schedules maybe_retrain_for_dataset via
BackgroundTasks whenever a live-refreshed Dataset's content changes (Task 4
wires that trigger; this module owns the decision policy on its own).

Policy (full rationale in docs/superpowers/specs/2026-07-18-automl-drift-
retrain-design.md): only runs with auto_retrain=True are watched. Drift is
checked with the existing PSI-based check_data_drift against each run's own
training-time sample. "high" severity retrains; a quality gate only
promotes the retrain (hands auto_retrain to the child) if its primary
metric is not worse than the parent's; MAX_AUTO_RETRAIN_STREAK consecutive
non-promoted retrains trips a circuit breaker that disables auto_retrain.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.middleware.audit import log_event
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import automl_engine, automl_runner, automl_storage
from app.services.datasets import dataframe_for

MAX_AUTO_RETRAIN_STREAK = 3
DRIFT_SEVERITY_THRESHOLD = "high"
COOLDOWN = timedelta(hours=1)


async def maybe_retrain_for_dataset(org_id: str, dataset_id: str) -> None:
    async with db_session.async_session_factory() as db:
        await _maybe_retrain(db, uuid.UUID(org_id), uuid.UUID(dataset_id))


async def _maybe_retrain(db: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID) -> None:
    watched = (
        await db.execute(
            select(AutoMLRun).where(
                AutoMLRun.org_id == org_id,
                AutoMLRun.dataset_id == dataset_id,
                AutoMLRun.status == "success",
                AutoMLRun.auto_retrain.is_(True),
            )
        )
    ).scalars().all()
    if not watched:
        return

    dataset = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id))
    ).scalar_one_or_none()
    if dataset is None:
        return

    for run in watched:
        await _evaluate_run(db, run, dataset)


async def _evaluate_run(db: AsyncSession, run: AutoMLRun, dataset: Dataset, *, skip_cooldown: bool = False) -> None:
    """skip_cooldown exists only so tests can exercise several consecutive
    evaluations without sleeping an hour between them - production code
    never passes it."""
    if run.auto_retrain_streak >= MAX_AUTO_RETRAIN_STREAK:
        return  # defensive guard - normally the breaker trips inline below,
        # so a still-watched run should never actually reach this streak.

    if not skip_cooldown:
        cutoff = datetime.now(timezone.utc) - COOLDOWN
        recent = (
            await db.execute(
                select(AutoMLRun.id).where(
                    AutoMLRun.org_id == run.org_id,
                    AutoMLRun.dataset_id == run.dataset_id,
                    AutoMLRun.retrained_from_run_id.isnot(None),
                    AutoMLRun.created_at >= cutoff,
                )
            )
        ).first()
        if recent is not None:
            return

    Xs, _ys = automl_storage.load_explain_sample(run.org_id, run.id)
    df_new = dataframe_for(dataset)
    try:
        drift = automl_engine.check_data_drift(run.meta or {}, Xs, df_new)
    except ValueError:
        return  # explain sample missing/unusable - nothing to compare, skip silently

    if drift["overall_severity"] != DRIFT_SEVERITY_THRESHOLD:
        return

    child = await automl_runner.create_run(
        db, org_id=run.org_id, created_by=run.created_by, dataset_id=run.dataset_id,
        name=f"{run.name} (auto-retrain)", task=run.task, target_column=run.target_column,
        feature_columns=run.feature_columns, config=run.config,
        auto_retrain=True, retrained_from_run_id=run.id,
    )
    await db.commit()

    await automl_runner.execute_automl_run(str(child.id), str(run.org_id))

    await db.refresh(child)
    await db.refresh(run)

    old_score = (
        automl_engine.normalize_higher_is_better(automl_engine.AutoMLTask(run.task), run.metric_value)
        if run.metric_value is not None else float("-inf")
    )
    new_score = (
        automl_engine.normalize_higher_is_better(automl_engine.AutoMLTask(child.task), child.metric_value)
        if child.status == "success" and child.metric_value is not None else float("-inf")
    )

    if new_score >= old_score:
        child.auto_retrain = True
        child.auto_retrain_streak = 0
        run.auto_retrain = False
        await log_event(
            db, action="automl.auto_retrain_promoted", org_id=run.org_id, actor_user_id=run.created_by,
            target=str(child.id),
            metadata={"from_run_id": str(run.id), "drift_severity": drift["overall_severity"]},
        )
    else:
        child.auto_retrain = False
        run.auto_retrain_streak += 1
        if run.auto_retrain_streak >= MAX_AUTO_RETRAIN_STREAK:
            run.auto_retrain = False
            await log_event(
                db, action="automl.auto_retrain_circuit_broken", org_id=run.org_id, actor_user_id=run.created_by,
                target=str(run.id),
                metadata={"new_run_id": str(child.id), "streak": run.auto_retrain_streak},
            )
        else:
            await log_event(
                db, action="automl.auto_retrain_rejected", org_id=run.org_id, actor_user_id=run.created_by,
                target=str(child.id),
                metadata={"from_run_id": str(run.id), "drift_severity": drift["overall_severity"]},
            )
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py -v`
Expected: all PASS. If `test_high_drift_triggers_promoted_retrain` or the circuit-breaker test is flaky because the synthetic data doesn't produce a clean PSI "high" severity, adjust `_classification_csv`'s row count / `offset` magnitude (not the production code) until the drift is reliably detected — the assertion shapes in the tests are correct as written; only the fixture data may need tuning to reliably provoke the same PSI outcome each run.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/automl_retrain.py backend/tests/test_automl_retrain.py
git commit -m "feat: add automl_retrain service (drift check, quality gate, circuit breaker)"
```

---

### Task 4: Wire the trigger — `overwrite_dataset_file` + downtime tracking + shift forecast

**Files:**
- Modify: `backend/app/services/datasets.py`
- Modify: `backend/app/services/downtime_tracking.py`
- Modify: `backend/app/api/routes/downtime_tracking.py`
- Modify: `backend/app/services/shifts.py`
- Modify: `backend/app/api/routes/shifts.py`
- Test: `backend/tests/test_automl_retrain.py` (append)

**Interfaces:**
- Consumes: `app.services.automl_retrain.maybe_retrain_for_dataset` (Task 3).
- Produces: `app.services.datasets.overwrite_dataset_file(db, dataset, *, data, background_tasks=None)` — same signature plus one new optional kwarg.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_automl_retrain.py`:

```python
async def test_incident_close_schedules_retrain_check_end_to_end(client, test_engine, monkeypatch):
    """Full path: real HTTP incident-close -> overwrite_dataset_file ->
    BackgroundTasks -> maybe_retrain_for_dataset. Patches
    maybe_retrain_for_dataset itself (not its internals) so this test
    verifies the WIRING (was it called, with what args), not the drift
    policy already covered by Task 3's tests."""
    from app.services import automl_retrain

    calls: list[tuple[str, str]] = []

    async def _fake_retrain(org_id: str, dataset_id: str) -> None:
        calls.append((org_id, dataset_id))

    monkeypatch.setattr(automl_retrain, "maybe_retrain_for_dataset", _fake_retrain)

    token = await _register_and_login_local(client, "arwiring@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]
    ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
    dataset_id = ended.json()["live_dataset_id"]
    assert calls == []  # first materialization is a create, not an overwrite - no trigger yet

    start2 = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 2", "reason": "other", "shift": "A"},
    )
    await client.post(f"/downtime-tracking/incidents/{start2.json()['id']}/end", headers=headers)

    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    assert calls == [(org_id, dataset_id)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py::test_incident_close_schedules_retrain_check_end_to_end -v`
Expected: FAIL — `calls` stays empty after the second incident closes (nothing schedules `maybe_retrain_for_dataset` yet).

- [ ] **Step 3: Add `background_tasks` to `overwrite_dataset_file`**

In `backend/app/services/datasets.py`, find:

```python
async def overwrite_dataset_file(db: AsyncSession, dataset: Dataset, *, data: bytes) -> Dataset:
    """Rewrite an existing, already-created dataset's file in place and
    re-sniff its schema/row count. Used by live-capture features (the
    downtime-tracking board) whose Dataset keeps growing instead of being
    created once - see storage.replace_dataset_file for the atomicity note."""
    storage.replace_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext, data)
    columns, row_count = _sniff(storage.dataset_path(dataset.org_id, dataset.id, dataset.storage_ext))
    dataset.columns = columns
    dataset.row_count = row_count
    dataset.size_bytes = len(data)
    await db.flush()
    return dataset
```

Replace with:

```python
async def overwrite_dataset_file(
    db: AsyncSession, dataset: Dataset, *, data: bytes, background_tasks: "BackgroundTasks | None" = None
) -> Dataset:
    """Rewrite an existing, already-created dataset's file in place and
    re-sniff its schema/row count. Used by live-capture features (the
    downtime-tracking board, shift-forecast source) whose Dataset keeps
    growing/refreshing instead of being created once - see
    storage.replace_dataset_file for the atomicity note.

    When background_tasks is provided, also schedules a check of whether
    any AutoML run watching this dataset (AutoMLRun.auto_retrain=True)
    should be retrained for drift - see app.services.automl_retrain. The
    import below is local, not at module level, specifically to avoid a
    circular import: automl_retrain imports dataframe_for from this same
    module at its own module level.
    """
    storage.replace_dataset_file(dataset.org_id, dataset.id, dataset.storage_ext, data)
    columns, row_count = _sniff(storage.dataset_path(dataset.org_id, dataset.id, dataset.storage_ext))
    dataset.columns = columns
    dataset.row_count = row_count
    dataset.size_bytes = len(data)
    await db.flush()
    if background_tasks is not None:
        from app.services import automl_retrain

        background_tasks.add_task(
            automl_retrain.maybe_retrain_for_dataset, str(dataset.org_id), str(dataset.id)
        )
    return dataset
```

Then find the import block near the top of the file:

```python
import duckdb
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
```

Replace with:

```python
import duckdb
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
```

- [ ] **Step 4: Thread `background_tasks` through downtime tracking**

In `backend/app/services/downtime_tracking.py`, find:

```python
async def _materialize(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
```

Replace with:

```python
async def _materialize(
    db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID, background_tasks: "BackgroundTasks | None" = None
) -> uuid.UUID:
```

Then find:

```python
    dataset = await ds_service.resolve_dataset(db, org_id, str(live.dataset_id))
    await ds_service.overwrite_dataset_file(db, dataset, data=csv_bytes)
    return dataset.id
```

Replace with:

```python
    dataset = await ds_service.resolve_dataset(db, org_id, str(live.dataset_id))
    await ds_service.overwrite_dataset_file(db, dataset, data=csv_bytes, background_tasks=background_tasks)
    return dataset.id
```

Then find:

```python
async def end_incident(
    db: AsyncSession, incident: DowntimeIncident, *, org_id: uuid.UUID, created_by: uuid.UUID
) -> uuid.UUID:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already ended")
    incident.ended_at = datetime.now(timezone.utc)
    await db.flush()
    return await _materialize(db, org_id=org_id, created_by=created_by)
```

Replace with:

```python
async def end_incident(
    db: AsyncSession,
    incident: DowntimeIncident,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    background_tasks: "BackgroundTasks | None" = None,
) -> uuid.UUID:
    if incident.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already ended")
    incident.ended_at = datetime.now(timezone.utc)
    await db.flush()
    return await _materialize(db, org_id=org_id, created_by=created_by, background_tasks=background_tasks)
```

Then find the import block near the top of the file:

```python
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
```

Replace with:

```python
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
```

In `backend/app/api/routes/downtime_tracking.py`, find:

```python
@router.post("/incidents/{incident_id}/end", response_model=IncidentEndResponse)
async def end_incident_route(
    incident_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    dataset_id = await dt_service.end_incident(db, incident, org_id=current.org_id, created_by=current.id)
    await db.commit()
    await db.refresh(incident)
    return IncidentEndResponse(incident=_to_response(incident), live_dataset_id=str(dataset_id))
```

Replace with:

```python
@router.post("/incidents/{incident_id}/end", response_model=IncidentEndResponse)
async def end_incident_route(
    incident_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    dataset_id = await dt_service.end_incident(
        db, incident, org_id=current.org_id, created_by=current.id, background_tasks=background_tasks
    )
    await db.commit()
    await db.refresh(incident)
    return IncidentEndResponse(incident=_to_response(incident), live_dataset_id=str(dataset_id))
```

Then find the import line:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
```

Replace with:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
```

- [ ] **Step 5: Thread `background_tasks` through shift-forecast source**

In `backend/app/services/shifts.py`, find:

```python
async def prepare_forecast_source(
    db: AsyncSession,
    dataset: Dataset,
    req: Any,
    *,
    shift: str,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
) -> dict:
```

Replace with:

```python
async def prepare_forecast_source(
    db: AsyncSession,
    dataset: Dataset,
    req: Any,
    *,
    shift: str,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    background_tasks: "BackgroundTasks | None" = None,
) -> dict:
```

Then find:

```python
    else:
        forecast_dataset = await ds_service.resolve_dataset(db, org_id, str(mapping.dataset_id))
        await ds_service.overwrite_dataset_file(db, forecast_dataset, data=csv_bytes)
        dataset_id = forecast_dataset.id
```

Replace with:

```python
    else:
        forecast_dataset = await ds_service.resolve_dataset(db, org_id, str(mapping.dataset_id))
        await ds_service.overwrite_dataset_file(
            db, forecast_dataset, data=csv_bytes, background_tasks=background_tasks
        )
        dataset_id = forecast_dataset.id
```

Then find the imports at the top of `shifts.py` added by the prior shift-forecast feature:

```python
import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shift_forecast import ShiftForecastDataset
```

Replace with:

```python
import csv
import io
import uuid

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shift_forecast import ShiftForecastDataset
```

In `backend/app/api/routes/shifts.py`, find:

```python
@router.post("/shifts/forecast-source", response_model=ShiftForecastSourceResponse)
async def shift_forecast_source(
    req: ShiftForecastSourceRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [req.shift_col, req.machine_col, req.timestamp_col])
    result = await shifts_service.prepare_forecast_source(
        db, dataset, req, shift=req.shift, org_id=current.org_id, created_by=current.id
    )
    await db.commit()
```

Replace with:

```python
@router.post("/shifts/forecast-source", response_model=ShiftForecastSourceResponse)
async def shift_forecast_source(
    req: ShiftForecastSourceRequest,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [req.shift_col, req.machine_col, req.timestamp_col])
    result = await shifts_service.prepare_forecast_source(
        db, dataset, req, shift=req.shift, org_id=current.org_id, created_by=current.id,
        background_tasks=background_tasks,
    )
    await db.commit()
```

Then find the import line at the top of `backend/app/api/routes/shifts.py`:

```python
from fastapi import APIRouter, Depends
```

Replace with:

```python
from fastapi import APIRouter, BackgroundTasks, Depends
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_automl_retrain.py tests/test_downtime_tracking.py tests/test_shift_forecast.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full backend test suite to check for regressions**

Run: `cd backend && python -m pytest -q`
Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/datasets.py backend/app/services/downtime_tracking.py backend/app/api/routes/downtime_tracking.py backend/app/services/shifts.py backend/app/api/routes/shifts.py backend/tests/test_automl_retrain.py
git commit -m "feat: trigger drift-retrain check on live dataset overwrite"
```

---

### Task 5: Frontend — opt-in checkbox, lineage note, list badge

**Files:**
- Modify: `frontend/src/pages/automl/api.ts`
- Modify: `frontend/src/pages/automl/AutoMLRun.tsx`
- Modify: `frontend/src/pages/AutoML.tsx`

**Interfaces:**
- Consumes: backend's `auto_retrain: bool` / `retrained_from_run_id: string | null` fields on `RunSummary`/`RunDetail` (Task 2), unchanged `TrainRequest` shape otherwise.

- [ ] **Step 1: Add the fields to the frontend types and train request**

In `frontend/src/pages/automl/api.ts`, find:

```ts
export interface AutoMLRunSummary {
  id: string;
  name: string;
  task: AutoMLTaskType;
  status: "queued" | "running" | "success" | "failed";
  dataset_id: string;
  target_column: string;
  best_model_name: string | null;
  metric_name: string | null;
  metric_value: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
```

Replace with:

```ts
export interface AutoMLRunSummary {
  id: string;
  name: string;
  task: AutoMLTaskType;
  status: "queued" | "running" | "success" | "failed";
  dataset_id: string;
  target_column: string;
  best_model_name: string | null;
  metric_name: string | null;
  metric_value: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  auto_retrain: boolean;
  retrained_from_run_id: string | null;
}
```

Then find:

```ts
export interface TrainRequest {
  name: string;
  dataset_id: string;
  task: AutoMLTaskType;
  target_column: string;
  feature_columns: string[];
  thoroughness: string;
  algorithms: string[] | null;
  handle_class_imbalance: boolean;
  time_column: string | null;
  horizon: number;
  forecast_strategy: "direct" | "recursive";
  time_budget_seconds: number | null;
  search_method: "random" | "bayesian";
}
```

Replace with:

```ts
export interface TrainRequest {
  name: string;
  dataset_id: string;
  task: AutoMLTaskType;
  target_column: string;
  feature_columns: string[];
  thoroughness: string;
  algorithms: string[] | null;
  handle_class_imbalance: boolean;
  time_column: string | null;
  horizon: number;
  forecast_strategy: "direct" | "recursive";
  time_budget_seconds: number | null;
  search_method: "random" | "bayesian";
  auto_retrain: boolean;
}
```

- [ ] **Step 2: Add the checkbox to the training form and wire the submit body**

In `frontend/src/pages/automl/AutoMLRun.tsx`, find:

```tsx
  const [timeColumn, setTimeColumn] = useState("");
  const [horizon, setHorizon] = useState(7);
  const [forecastStrategy, setForecastStrategy] = useState<"direct" | "recursive">("direct");
  const [error, setError] = useState<string | null>(null);
```

Replace with:

```tsx
  const [timeColumn, setTimeColumn] = useState("");
  const [horizon, setHorizon] = useState(7);
  const [forecastStrategy, setForecastStrategy] = useState<"direct" | "recursive">("direct");
  const [autoRetrain, setAutoRetrain] = useState(false);
  const [error, setError] = useState<string | null>(null);
```

Then find:

```tsx
        time_budget_seconds: timeBudgetSeconds ? Number(timeBudgetSeconds) : null,
        search_method: searchMethod,
      },
```

Replace with:

```tsx
        time_budget_seconds: timeBudgetSeconds ? Number(timeBudgetSeconds) : null,
        search_method: searchMethod,
        auto_retrain: autoRetrain,
      },
```

Then find:

```tsx
              {task === "classification" && (
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={handleImbalance} onChange={(e) => setHandleImbalance(e.target.checked)} />
                    Handle class imbalance (class_weight="balanced")
                  </label>
                </div>
              )}
```

Replace with:

```tsx
              {task === "classification" && (
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={handleImbalance} onChange={(e) => setHandleImbalance(e.target.checked)} />
                    Handle class imbalance (class_weight="balanced")
                  </label>
                </div>
              )}
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm" title="When this dataset's data changes and drift is detected, automatically retrain and promote if the new model isn't worse.">
                  <input type="checkbox" checked={autoRetrain} onChange={(e) => setAutoRetrain(e.target.checked)} />
                  Auto-retrain on drift
                </label>
              </div>
```

- [ ] **Step 3: Show lineage on the run detail page**

In `frontend/src/pages/automl/AutoMLRun.tsx`, find:

```tsx
      {!isNew && isFailed && <Alert variant="destructive">{runData?.error ?? "Training failed."}</Alert>}

      {!isNew && isDone && runData && (
```

Replace with:

```tsx
      {!isNew && isFailed && <Alert variant="destructive">{runData?.error ?? "Training failed."}</Alert>}

      {!isNew && runData?.retrained_from_run_id && (
        <p className="text-xs text-muted-foreground">
          Auto-retrained from{" "}
          <Link to={`/automl/${runData.retrained_from_run_id}`} className="underline underline-offset-2">
            an earlier run
          </Link>
          .
        </p>
      )}

      {!isNew && isDone && runData && (
```

This requires `Link` from `react-router-dom` — check the top of the file for an existing import; if `Link` isn't already imported there (it likely isn't, since this page uses `useNavigate`/`useParams` only), find:

```tsx
import { useNavigate, useParams } from "react-router-dom";
```

Replace with:

```tsx
import { Link, useNavigate, useParams } from "react-router-dom";
```

(If `Link` is already imported, skip this substep — don't create a duplicate import.)

- [ ] **Step 4: Add a badge on the run list**

In `frontend/src/pages/AutoML.tsx`, find:

```tsx
                  <TD className="font-medium">
                    <Link to={`/automl/${r.id}`} className="hover:underline">
                      {r.name}
                    </Link>
                  </TD>
```

Replace with:

```tsx
                  <TD className="font-medium">
                    <Link to={`/automl/${r.id}`} className="hover:underline">
                      {r.name}
                    </Link>
                    {r.auto_retrain && (
                      <Badge variant="info" className="ml-2">
                        auto-retrain
                      </Badge>
                    )}
                  </TD>
```

`Badge` is already imported at the top of this file (used for `STATUS_VARIANT`).

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 6: Manual in-browser verification**

Start the backend and frontend dev servers, log in:

1. Go to Datasets, upload a small classification CSV (2 numeric feature columns + a 0/1 label column, ~60 rows).
2. Go to AutoML → New run, select that dataset, task=classification, target=the label column, thoroughness=fast, check "Auto-retrain on drift", train. Confirm it succeeds and the run list shows an "auto-retrain" badge next to it.
3. Go to Datasets, re-upload/replace enough of the same dataset's rows with a flipped label rule (or use the Downtime Tracking / Shift Forecast live-refresh flows, which are the two paths that actually call `overwrite_dataset_file` in this codebase — a plain re-upload via `/datasets` creates a new dataset, it does not overwrite, so it will **not** trigger this feature; use one of the two live paths instead to observe the trigger).
4. Confirm a second run appears in the AutoML list shortly after, with "Auto-retrained from an earlier run" on its detail page, and that exactly one of the two runs (parent/child) carries the "auto-retrain" badge afterward.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/automl/api.ts frontend/src/pages/automl/AutoMLRun.tsx frontend/src/pages/AutoML.tsx
git commit -m "feat: add auto-retrain opt-in, lineage note, and list badge to AutoML UI"
```
