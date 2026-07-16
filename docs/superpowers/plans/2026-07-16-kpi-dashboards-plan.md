# KPI Dashboards & Breakdown Drill-Down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the flat, manual-compute KPI table into a live dashboard system: structured per-machine/breach-Pareto breakdown on any KPI, tiered auto-refreshing card dashboards, and close the missing RBAC guard on KPI mutation routes.

**Architecture:** Two additive backend changes (breakdown columns on `Kpi`, new `KpiDashboard` model) sit behind the existing `services/kpis.py` compute path, extended rather than replaced. Frontend adds a picker-based breakdown config to the existing KPI form and a new `KpiDashboards.tsx` page that reuses `EngineScreen`'s fullscreen pattern and React Query's native `refetchInterval` for auto-refresh.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + DuckDB (backend), React + TanStack Query + Tailwind (frontend), pytest (backend tests only — no frontend test suite exists in this codebase).

## Global Constraints

- Every new SQL-building path uses the allow-list + `qi()`-quote pattern from `app/services/datasets.py` (`ds_service.qi`, `ds_service.validate_columns`) — never raw string interpolation of user-supplied column names.
- All new mutating routes require `Depends(require_role("owner"))`, matching every other resource route in this codebase (`datasets.py`, `items.py`, `boms.py`).
- No new frontend dependency — no charting library exists in this codebase today; hand-rolled SVG components only.
- Follow the exact CRUD/response shape `api/routes/kpis.py` already uses for the new `kpi_dashboards.py` router.

---

### Task 1: `Kpi` breakdown columns — migration + model

**Files:**
- Create: `backend/alembic/versions/d4e8f21a6b39_kpi_breakdown_columns.py`
- Modify: `backend/app/models/kpi.py`

**Interfaces:**
- Produces: `Kpi.breakdown_group_col: str | None`, `Kpi.breakdown_agg: str | None`, `Kpi.breakdown_value_col: str | None` — consumed by Task 2 (compute) and Task 3 (API schema).

- [ ] **Step 1: Write the migration**

```python
"""kpi breakdown columns

Revision ID: d4e8f21a6b39
Revises: a2f7c5e91d84
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e8f21a6b39"  # pragma: allowlist secret
down_revision = "a2f7c5e91d84"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kpis", sa.Column("breakdown_group_col", sa.String(length=200), nullable=True))
    op.add_column("kpis", sa.Column("breakdown_agg", sa.String(length=10), nullable=True))
    op.add_column("kpis", sa.Column("breakdown_value_col", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("kpis", "breakdown_value_col")
    op.drop_column("kpis", "breakdown_agg")
    op.drop_column("kpis", "breakdown_group_col")
```

- [ ] **Step 2: Run the migration**

Run (from `backend/`): `python -m alembic upgrade head`
Expected: no errors; `python -m alembic heads` now prints `d4e8f21a6b39 (head)`

- [ ] **Step 3: Add the columns to the `Kpi` model**

In `backend/app/models/kpi.py`, change the import line:

```python
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, String, Uuid
```
to:
```python
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Uuid
```

Then, in the `Kpi` class, after the `tags` column (`tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)`), add:

```python
    # Optional grouped drill-down query, run alongside the main value. Serves
    # two uses depending on what column is picked: GROUP BY machine + avg for
    # per-machine breakdown, or GROUP BY reason + count for a breach Pareto.
    # Column names are validated against the dataset's schema at write time
    # (see api/routes/kpis.py _validate_breakdown) - never raw SQL.
    breakdown_group_col: Mapped[str | None] = mapped_column(String(200), nullable=True)
    breakdown_agg: Mapped[str | None] = mapped_column(String(10), nullable=True)  # count | avg | sum
    breakdown_value_col: Mapped[str | None] = mapped_column(String(200), nullable=True)
```

- [ ] **Step 4: Verify the model imports cleanly**

Run: `python -c "from app.models.kpi import Kpi; print(Kpi.breakdown_group_col)"` (from `backend/`)
Expected: prints the SQLAlchemy column descriptor with no import error

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/d4e8f21a6b39_kpi_breakdown_columns.py backend/app/models/kpi.py
git commit -m "feat: add breakdown config columns to Kpi model"
```

---

### Task 2: Breakdown compute logic in `services/kpis.py`

**Files:**
- Modify: `backend/app/services/kpis.py`
- Test: `backend/tests/test_kpis.py`

**Interfaces:**
- Consumes: `Kpi.breakdown_group_col/breakdown_agg/breakdown_value_col` (Task 1), `ds_service.qi`, `ds_service.validate_columns` (existing, already imported as `ds_service` in this file), `run_sql` (existing import).
- Produces: `KpiComputeItem.breakdown: list[tuple[str, float]] | None` — consumed by Task 3 (`_item_to_response`) and Task 5 (`compute_kpis_multi_dataset`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kpis.py`:

```python
CSV_BREAKDOWN = b"machine_id,scrap_units,total_units,reason\nM1,5,100,jam\nM1,3,100,jam\nM2,8,100,misfeed\nM2,2,100,jam\n"


async def test_kpi_breakdown_count_pareto(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap2.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            "breakdown_group_col": "reason",
            "breakdown_agg": "count",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    item = computed.json()[0]
    assert item["breakdown"] is not None
    labels = {b["label"]: b["value"] for b in item["breakdown"]}
    assert labels["jam"] == 3
    assert labels["misfeed"] == 1


async def test_kpi_breakdown_avg_per_group(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap3.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Units",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "machine_id",
            "breakdown_agg": "avg",
            "breakdown_value_col": "scrap_units",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    item = computed.json()[0]
    labels = {b["label"]: b["value"] for b in item["breakdown"]}
    assert labels["M1"] == pytest.approx(4.0)
    assert labels["M2"] == pytest.approx(5.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `pytest tests/test_kpis.py -k breakdown -v`
Expected: FAIL — `create.status_code == 201` fails with a 422 (unknown fields `breakdown_group_col`/`breakdown_agg`/`breakdown_value_col` not yet accepted by the request schema — that's Task 3, but the compute logic needs to exist first for Task 3's wiring to have something to call)

- [ ] **Step 3: Add the breakdown SQL builder and compute helper**

In `backend/app/services/kpis.py`, after `_replace_table_placeholder` (around line 72), add:

```python
_BREAKDOWN_AGG_SQL = {"avg": "AVG", "sum": "SUM"}


def _breakdown_sql(group_col: str, agg: str, value_col: str | None) -> str:
    group_ref = ds_service.qi(group_col)
    if agg == "count":
        return f"SELECT {group_ref}, COUNT(*) FROM {{table}} GROUP BY {group_ref} ORDER BY 2 DESC"
    value_ref = ds_service.qi(value_col)
    return (
        f"SELECT {group_ref}, {_BREAKDOWN_AGG_SQL[agg]}(TRY_CAST({value_ref} AS DOUBLE)) "
        f"FROM {{table}} GROUP BY {group_ref} ORDER BY 2 DESC"
    )


async def _compute_breakdown(dataset: Dataset, kpi: Kpi) -> list[tuple[str, float]] | None:
    """Runs alongside the main KPI value when a breakdown is configured -
    per-machine averages or a breach-cause Pareto, depending on what column
    the author picked. Fails soft: a bad breakdown query degrades to no
    breakdown rather than failing the whole KPI compute."""
    if not kpi.breakdown_group_col:
        return None
    try:
        ds_service.validate_columns(
            dataset,
            [kpi.breakdown_group_col, kpi.breakdown_value_col] if kpi.breakdown_value_col else [kpi.breakdown_group_col],
        )
        sql = _replace_table_placeholder(
            _breakdown_sql(kpi.breakdown_group_col, kpi.breakdown_agg or "count", kpi.breakdown_value_col)
        )
        result = await run_sql(dataset, sql, row_limit=10)
    except (HTTPException, ValueError):
        return None
    out: list[tuple[str, float]] = []
    for row in result["rows"]:
        vals = list(row.values())
        if len(vals) < 2:
            continue
        v = _safe_float(vals[1])
        if v is not None:
            out.append((str(vals[0]), v))
    return out or None
```

Add the two imports this needs at the top of the file:

```python
from app.models.dataset import Dataset
```
(add alongside the existing `from app.models.dataset import Dataset` check — if not already present; `app.models.kpi` is already imported, `Dataset` is not, so add this line near the other `from app.models...` imports)

- [ ] **Step 4: Add `breakdown` to `KpiComputeItem` and wire it into `_compute_one`**

Change the `KpiComputeItem` dataclass:

```python
@dataclass
class KpiComputeItem:
    kpi_id: uuid.UUID
    value: float | None
    meta: dict
    computed_at: datetime
    cache_hit: bool
    error: str | None = None
    eval: dict | None = None
    breakdown: list[tuple[str, float]] | None = None
```

In `_compute_one`, the cache-hit branch:

```python
    if cached is not None and cached.error is None:
        history = await get_history_values(db, org_id, kpi.id, dataset.id, lookback + 1)
        prev = history[1].value if len(history) > 1 else None
        hist_vals = [h.value for h in reversed(history) if h.value is not None]
        eval_res = (
            _evaluate_status(kpi.eval_policy, cached.value, prev, hist_vals) if include_eval else None
        )
        breakdown = await _compute_breakdown(dataset, kpi)
        return KpiComputeItem(
            kpi_id=kpi.id,
            value=cached.value,
            meta=cached.meta,
            computed_at=cached.computed_at,
            cache_hit=True,
            error=None,
            eval=eval_res,
            breakdown=breakdown,
        )
```

And the fresh-compute return at the end of the function:

```python
    history = await get_history_values(db, org_id, kpi.id, dataset.id, lookback + 1)
    prev = history[1].value if len(history) > 1 else None
    hist_vals = [h.value for h in reversed(history) if h.value is not None]
    eval_res = _evaluate_status(kpi.eval_policy, value, prev, hist_vals) if include_eval else None
    breakdown = await _compute_breakdown(dataset, kpi)

    return KpiComputeItem(
        kpi_id=kpi.id, value=value, meta=meta, computed_at=kv.computed_at, cache_hit=False, error=None,
        eval=eval_res, breakdown=breakdown,
    )
```

(The disabled-KPI and SQL-error early returns are unchanged — `breakdown` defaults to `None`.)

- [ ] **Step 5: Run tests to verify they still fail at the same point**

Run: `pytest tests/test_kpis.py -k breakdown -v`
Expected: still FAIL at `create.status_code == 201` (422) — Task 3 wires the request schema next. This confirms the compute layer change didn't break anything importable.

Run: `pytest tests/test_kpis.py -v` (full file, no filter)
Expected: all previously-passing tests still PASS (regression check)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/kpis.py backend/tests/test_kpis.py
git commit -m "feat: compute optional grouped breakdown alongside a KPI's main value"
```

---

### Task 3: Breakdown fields + RBAC in `api/routes/kpis.py`

**Files:**
- Modify: `backend/app/api/routes/kpis.py`
- Test: `backend/tests/test_kpis.py`

**Interfaces:**
- Consumes: `Kpi.breakdown_*` (Task 1), `KpiComputeItem.breakdown` (Task 2), `ds_service.validate_columns`, `require_role` (existing in `app.api.deps`, not yet imported here).
- Produces: `KpiResponse.breakdown_group_col/breakdown_agg/breakdown_value_col`, `KpiComputeItemResponse.breakdown: list[dict] | None` — consumed by Task 8 (frontend form) and Task 9 (frontend dashboard cards).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kpis.py` (this makes the two Task 2 tests pass too, since they depend on the request schema accepting the new fields):

```python
async def test_kpi_breakdown_avg_requires_value_col(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap4.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Bad breakdown",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "machine_id",
            "breakdown_agg": "avg",
        },
    )
    assert create.status_code == 400


async def test_kpi_breakdown_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap5.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Bad column",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "not_a_real_column",
            "breakdown_agg": "count",
        },
    )
    assert create.status_code == 400


async def test_rbac_blocks_non_owner_from_kpi_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    dataset_id = await _upload(client, token)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member3@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member3@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.post(
        "/kpis",
        headers=member_headers,
        json={"name": "x", "dataset_id": dataset_id, "sql": "SELECT 1 FROM {table}"},
    )
    assert res.status_code == 403

    res = await client.post("/kpis/seed/manufacturing", headers=member_headers, json={"dataset_id": dataset_id})
    assert res.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kpis.py -v`
Expected: FAIL — breakdown tests get 422 (unknown request fields), RBAC test gets 201 instead of 403 (no role guard yet)

- [ ] **Step 3: Add imports**

At the top of `backend/app/api/routes/kpis.py`, change:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.kpi import Kpi, KpiValue
from app.services import datasets as ds_service
from app.services import kpis as kpi_service
```

to:

```python
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.dataset import Dataset
from app.models.kpi import Kpi, KpiValue
from app.services import datasets as ds_service
from app.services import kpis as kpi_service
```

- [ ] **Step 4: Add breakdown fields to the request/response schemas**

In `KpiWriteRequest`, after `tags: list[str] = Field(default_factory=list)`, add:

```python
    breakdown_group_col: str | None = None
    breakdown_agg: Literal["count", "avg", "sum"] | None = None
    breakdown_value_col: str | None = None
```

In `KpiPatchRequest`, after `tags: list[str] | None = None`, add the same three fields (all `| None = None`, already optional by construction):

```python
    breakdown_group_col: str | None = None
    breakdown_agg: Literal["count", "avg", "sum"] | None = None
    breakdown_value_col: str | None = None
```

In `KpiResponse`, after `tags: list[str]`, add:

```python
    breakdown_group_col: str | None
    breakdown_agg: str | None
    breakdown_value_col: str | None
```

In `KpiComputeItemResponse`, after `eval: dict | None`, add:

```python
    breakdown: list[dict] | None
```

- [ ] **Step 5: Add the validation helper**

Before the `# KPI definitions` section comment, add:

```python
def _validate_breakdown(dataset: Dataset, group_col: str | None, agg: str | None, value_col: str | None) -> None:
    if not group_col:
        return
    cols = [group_col]
    if agg and agg != "count":
        if not value_col:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "breakdown_value_col is required when breakdown_agg is not 'count'")
        cols.append(value_col)
    ds_service.validate_columns(dataset, cols)
```

- [ ] **Step 6: Update `_kpi_to_response` and `_item_to_response`**

```python
def _kpi_to_response(k: Kpi) -> KpiResponse:
    return KpiResponse(
        id=str(k.id),
        dataset_id=str(k.dataset_id),
        name=k.name,
        description=k.description,
        sql=k.sql,
        unit=k.unit,
        direction=k.direction,
        enabled=k.enabled,
        eval_policy=k.eval_policy,
        tags=k.tags,
        breakdown_group_col=k.breakdown_group_col,
        breakdown_agg=k.breakdown_agg,
        breakdown_value_col=k.breakdown_value_col,
        created_at=k.created_at.isoformat(),
        updated_at=k.updated_at.isoformat(),
    )
```

```python
def _item_to_response(i: kpi_service.KpiComputeItem) -> KpiComputeItemResponse:
    return KpiComputeItemResponse(
        kpi_id=str(i.kpi_id), value=i.value, meta=i.meta, computed_at=i.computed_at.isoformat(),
        cache_hit=i.cache_hit, error=i.error, eval=i.eval,
        breakdown=[{"label": l, "value": v} for l, v in i.breakdown] if i.breakdown else None,
    )
```

- [ ] **Step 7: Wire validation + breakdown fields + RBAC into the route handlers**

Replace `create_kpi`:

```python
@router.post("", response_model=KpiResponse, status_code=status.HTTP_201_CREATED)
async def create_kpi(
    req: KpiWriteRequest, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
):
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    _validate_breakdown(dataset, req.breakdown_group_col, req.breakdown_agg, req.breakdown_value_col)
    kpi = Kpi(
        org_id=current.org_id,
        dataset_id=req.dataset_id,
        name=req.name,
        description=req.description,
        sql=req.sql,
        unit=req.unit,
        direction=req.direction,
        enabled=req.enabled,
        eval_policy=req.eval_policy.model_dump(exclude_none=True) if req.eval_policy else None,
        tags=req.tags,
        breakdown_group_col=req.breakdown_group_col,
        breakdown_agg=req.breakdown_agg,
        breakdown_value_col=req.breakdown_value_col,
        created_by=current.id,
    )
    db.add(kpi)
    await log_event(db, action="kpi.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name})
    await db.commit()
    await db.refresh(kpi)
    return _kpi_to_response(kpi)
```

Replace `update_kpi`:

```python
@router.patch("/{kpi_id}", response_model=KpiResponse)
async def update_kpi(
    kpi_id: uuid.UUID,
    patch: KpiPatchRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    kpi = await get_or_404(db, Kpi, current.org_id, kpi_id, "KPI not found")
    updates = patch.model_dump(exclude_unset=True)

    if "dataset_id" in updates:
        await ds_service.get_dataset_or_404(db, current.org_id, patch.dataset_id)
    if "eval_policy" in updates:
        updates["eval_policy"] = patch.eval_policy.model_dump(exclude_none=True) if patch.eval_policy else None

    if {"breakdown_group_col", "breakdown_agg", "breakdown_value_col"} & updates.keys():
        dataset = await ds_service.get_dataset_or_404(db, current.org_id, patch.dataset_id or kpi.dataset_id)
        _validate_breakdown(
            dataset,
            updates.get("breakdown_group_col", kpi.breakdown_group_col),
            updates.get("breakdown_agg", kpi.breakdown_agg),
            updates.get("breakdown_value_col", kpi.breakdown_value_col),
        )

    for field_name, value in updates.items():
        setattr(kpi, field_name, value)

    await log_event(db, action="kpi.updated", org_id=current.org_id, actor_user_id=current.id, target=str(kpi.id))
    await db.commit()
    await db.refresh(kpi)
    return _kpi_to_response(kpi)
```

Change `delete_kpi`'s dependency:
```python
    kpi_id: uuid.UUID, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
```

Change `seed_manufacturing_kpis`'s dependency:
```python
    req: MfgSeedRequest, current: CurrentUser = Depends(require_role("owner")), db: AsyncSession = Depends(get_db)
```

(`list_kpis`, `get_kpi`, `kpi_history`, `compute_kpis`, `kpi_cards`, `compare_kpis` keep `Depends(get_current_user)` — read-only routes stay open to any authenticated org member.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_kpis.py -v`
Expected: PASS — all tests in the file, including the two from Task 2 and the four from this task

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/routes/kpis.py backend/tests/test_kpis.py
git commit -m "feat: expose KPI breakdown config over the API, require owner role for KPI mutations"
```

---

### Task 4: `KpiDashboard` model + migration

**Files:**
- Create: `backend/alembic/versions/f1a5c8d3e942_kpi_dashboards.py`
- Modify: `backend/app/models/kpi.py`

**Interfaces:**
- Produces: `KpiDashboard` model (`id, org_id, name, tier, kpi_ids, refresh_seconds, created_by, created_at, updated_at`) — consumed by Task 6 (`api/routes/kpi_dashboards.py`).

- [ ] **Step 1: Write the migration**

```python
"""kpi dashboards

Revision ID: f1a5c8d3e942
Revises: d4e8f21a6b39
Create Date: 2026-07-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a5c8d3e942"  # pragma: allowlist secret
down_revision = "d4e8f21a6b39"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpi_dashboards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("kpi_ids", sa.JSON(), nullable=False),
        sa.Column("refresh_seconds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kpi_dashboards_org_id", "kpi_dashboards", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_kpi_dashboards_org_id", table_name="kpi_dashboards")
    op.drop_table("kpi_dashboards")
```

- [ ] **Step 2: Run the migration**

Run (from `backend/`): `python -m alembic upgrade head`
Expected: no errors; `python -m alembic heads` prints `f1a5c8d3e942 (head)`

- [ ] **Step 3: Add the model**

At the end of `backend/app/models/kpi.py`, add:

```python
class KpiDashboard(Base):
    """A named, ordered, tiered collection of KPIs for the dashboard view
    (frontend/src/pages/KpiDashboards.tsx). kpi_ids is an ordered JSON list -
    same pattern as Kpi.tags - no join table needed at this scale.
    """

    __tablename__ = "kpi_dashboards"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="supervisor")  # operator | supervisor
    kpi_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    refresh_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Verify the model imports cleanly**

Run: `python -c "from app.models.kpi import KpiDashboard; print(KpiDashboard.__tablename__)"` (from `backend/`)
Expected: prints `kpi_dashboards`

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/f1a5c8d3e942_kpi_dashboards.py backend/app/models/kpi.py
git commit -m "feat: add KpiDashboard model"
```

---

### Task 5: `compute_kpis_multi_dataset` in `services/kpis.py`

**Files:**
- Modify: `backend/app/services/kpis.py`
- Test: `backend/tests/test_kpi_dashboards.py` (created in Task 6, but the test for this function is written here since it exercises the service layer directly — actually written as part of Task 6's dashboard-cards test, which is the only consumer)

**Interfaces:**
- Consumes: `compute_kpis` (existing, unchanged), `Kpi` model.
- Produces: `compute_kpis_multi_dataset(db, org_id, kpi_ids, *, force, include_eval, lookback) -> list[KpiComputeItem]` — consumed by Task 6 (`api/routes/kpi_dashboards.py`).

- [ ] **Step 1: Add the function**

At the end of `backend/app/services/kpis.py`, add:

```python
async def compute_kpis_multi_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    kpi_ids: list[uuid.UUID],
    *,
    force: bool = False,
    include_eval: bool = False,
    lookback: int = 14,
) -> list[KpiComputeItem]:
    """Like compute_kpis, but each KPI runs against its OWN dataset_id rather
    than one shared dataset_id - needed for a dashboard whose KPIs span
    multiple datasets. Groups by dataset_id and calls compute_kpis once per
    group, so the single-dataset code path is unchanged."""
    rows = (
        await db.execute(select(Kpi.id, Kpi.dataset_id).where(Kpi.org_id == org_id, Kpi.id.in_(kpi_ids)))
    ).all()
    dataset_by_kpi = {r.id: r.dataset_id for r in rows}

    by_dataset: dict[uuid.UUID, list[uuid.UUID]] = {}
    for kpi_id in kpi_ids:
        dataset_id = dataset_by_kpi.get(kpi_id)
        if dataset_id is None:
            continue
        by_dataset.setdefault(dataset_id, []).append(kpi_id)

    items_by_id: dict[uuid.UUID, KpiComputeItem] = {}
    for dataset_id, ids in by_dataset.items():
        for item in await compute_kpis(db, org_id, dataset_id, ids, force=force, include_eval=include_eval, lookback=lookback):
            items_by_id[item.kpi_id] = item

    out: list[KpiComputeItem] = []
    for kpi_id in kpi_ids:
        if kpi_id in items_by_id:
            out.append(items_by_id[kpi_id])
        else:
            out.append(
                KpiComputeItem(
                    kpi_id=kpi_id, value=None, meta={}, computed_at=datetime.now(timezone.utc),
                    cache_hit=False, error="KPI not found",
                )
            )
    return out
```

- [ ] **Step 2: Verify it imports cleanly**

Run (from `backend/`): `python -c "from app.services.kpis import compute_kpis_multi_dataset; print(compute_kpis_multi_dataset)"`
Expected: prints the function object with no import error

(Behavioral test for this function is `test_dashboard_cards_spans_multiple_datasets` in Task 6 — it's the only caller, so testing it through the route it powers avoids a redundant direct-unit test.)

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/kpis.py
git commit -m "feat: add compute_kpis_multi_dataset for dashboards spanning multiple datasets"
```

---

### Task 6: `kpi_dashboards.py` router + registration

**Files:**
- Create: `backend/app/api/routes/kpi_dashboards.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_kpi_dashboards.py`

**Interfaces:**
- Consumes: `KpiDashboard` model (Task 4), `compute_kpis_multi_dataset` (Task 5), `require_role`, `get_or_404` (existing).
- Produces: `GET/POST /kpi-dashboards`, `PATCH/DELETE /kpi-dashboards/{id}`, `GET /kpi-dashboards/{id}/cards` returning `KpiDashboardCardResponse[]` with fields `kpi_id, dataset_id, name, unit, direction, value, meta, computed_at, cache_hit, error, eval, breakdown` — consumed by Task 9 (frontend `KpiDashboards.tsx`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_kpi_dashboards.py`:

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV_A = b"units_produced,operating_hours\n100,10\n"
CSV_B = b"good_units,total_units\n90,100\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content, filename):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def test_dashboard_cards_spans_multiple_datasets(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_a = await _upload(client, token, content=CSV_A, filename="a.csv")
    dataset_b = await _upload(client, token, content=CSV_B, filename="b.csv")

    kpi_a = (
        await client.post(
            "/kpis",
            headers=headers,
            json={
                "name": "Throughput",
                "dataset_id": dataset_a,
                "sql": "SELECT SUM(TRY_CAST(units_produced AS DOUBLE)) / NULLIF(SUM(TRY_CAST(operating_hours AS DOUBLE)), 0) FROM {table}",
            },
        )
    ).json()["id"]
    kpi_b = (
        await client.post(
            "/kpis",
            headers=headers,
            json={
                "name": "First Pass Yield",
                "dataset_id": dataset_b,
                "sql": "SELECT (SUM(TRY_CAST(good_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            },
        )
    ).json()["id"]

    dashboard = await client.post(
        "/kpi-dashboards",
        headers=headers,
        json={"name": "Plant overview", "tier": "supervisor", "kpi_ids": [kpi_a, kpi_b]},
    )
    assert dashboard.status_code == 201, dashboard.text
    dashboard_id = dashboard.json()["id"]
    assert dashboard.json()["refresh_seconds"] == 300

    cards = await client.get(f"/kpi-dashboards/{dashboard_id}/cards", headers=headers)
    assert cards.status_code == 200, cards.text
    by_name = {c["name"]: c for c in cards.json()}
    assert by_name["Throughput"]["value"] == pytest.approx(10.0)
    assert by_name["First Pass Yield"]["value"] == pytest.approx(90.0)
    assert by_name["Throughput"]["dataset_id"] == dataset_a
    assert by_name["First Pass Yield"]["dataset_id"] == dataset_b


async def test_dashboard_crud(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_A, filename="c.csv")
    kpi_id = (
        await client.post(
            "/kpis",
            headers=headers,
            json={"name": "K", "dataset_id": dataset_id, "sql": "SELECT 1.0 FROM {table}"},
        )
    ).json()["id"]

    created = await client.post(
        "/kpi-dashboards", headers=headers, json={"name": "Floor", "tier": "operator", "kpi_ids": [kpi_id]}
    )
    assert created.status_code == 201
    dashboard_id = created.json()["id"]
    assert created.json()["refresh_seconds"] == 15

    listed = await client.get("/kpi-dashboards", headers=headers)
    assert any(d["id"] == dashboard_id for d in listed.json())

    patched = await client.patch(
        f"/kpi-dashboards/{dashboard_id}", headers=headers, json={"name": "Floor renamed"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Floor renamed"

    deleted = await client.delete(f"/kpi-dashboards/{dashboard_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/kpi-dashboards", headers=headers)).json() == []


async def test_rbac_blocks_non_owner_from_dashboard_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member4@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member4@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.post(
        "/kpi-dashboards", headers=member_headers, json={"name": "x", "tier": "operator", "kpi_ids": []}
    )
    assert res.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `pytest tests/test_kpi_dashboards.py -v`
Expected: FAIL — `404` on `/kpi-dashboards` (route doesn't exist yet)

- [ ] **Step 3: Write the router**

Create `backend/app/api/routes/kpi_dashboards.py`:

```python
"""Named, ordered, tiered collections of KPIs for the dashboard view. See
app/services/kpis.py's compute_kpis_multi_dataset for why cards computes
each KPI against its own dataset_id instead of one shared one.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.kpi import Kpi, KpiDashboard
from app.services import kpis as kpi_service

router = APIRouter(prefix="/kpi-dashboards", tags=["kpi-dashboards"])

_DEFAULT_REFRESH = {"operator": 15, "supervisor": 300}


class KpiDashboardWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    tier: str = "supervisor"  # operator | supervisor
    kpi_ids: list[uuid.UUID] = Field(default_factory=list)
    refresh_seconds: int | None = None


class KpiDashboardPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tier: str | None = None
    kpi_ids: list[uuid.UUID] | None = None
    refresh_seconds: int | None = None


class KpiDashboardResponse(BaseModel):
    id: str
    name: str
    tier: str
    kpi_ids: list[str]
    refresh_seconds: int
    created_at: str
    updated_at: str


def _validate_tier(tier: str) -> None:
    if tier not in ("operator", "supervisor"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tier must be 'operator' or 'supervisor'")


def _to_response(d: KpiDashboard) -> KpiDashboardResponse:
    return KpiDashboardResponse(
        id=str(d.id), name=d.name, tier=d.tier, kpi_ids=[str(k) for k in d.kpi_ids],
        refresh_seconds=d.refresh_seconds, created_at=d.created_at.isoformat(), updated_at=d.updated_at.isoformat(),
    )


@router.get("", response_model=list[KpiDashboardResponse])
async def list_dashboards(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(KpiDashboard).where(KpiDashboard.org_id == current.org_id).order_by(KpiDashboard.name))
    ).scalars().all()
    return [_to_response(d) for d in rows]


@router.post("", response_model=KpiDashboardResponse, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    req: KpiDashboardWriteRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    _validate_tier(req.tier)
    dashboard = KpiDashboard(
        org_id=current.org_id,
        name=req.name,
        tier=req.tier,
        kpi_ids=[str(k) for k in req.kpi_ids],
        refresh_seconds=req.refresh_seconds or _DEFAULT_REFRESH[req.tier],
        created_by=current.id,
    )
    db.add(dashboard)
    await log_event(db, action="kpi_dashboard.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name})
    await db.commit()
    await db.refresh(dashboard)
    return _to_response(dashboard)


@router.patch("/{dashboard_id}", response_model=KpiDashboardResponse)
async def update_dashboard(
    dashboard_id: uuid.UUID,
    patch: KpiDashboardPatchRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    updates = patch.model_dump(exclude_unset=True)
    if "tier" in updates:
        _validate_tier(updates["tier"])
    if "kpi_ids" in updates:
        updates["kpi_ids"] = [str(k) for k in updates["kpi_ids"]]
    for field_name, value in updates.items():
        setattr(dashboard, field_name, value)
    await log_event(db, action="kpi_dashboard.updated", org_id=current.org_id, actor_user_id=current.id, target=str(dashboard.id))
    await db.commit()
    await db.refresh(dashboard)
    return _to_response(dashboard)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    await db.delete(dashboard)
    await log_event(db, action="kpi_dashboard.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(dashboard_id))
    await db.commit()


class KpiDashboardCardResponse(BaseModel):
    kpi_id: str
    dataset_id: str
    name: str
    unit: str | None
    direction: str
    value: float | None
    meta: dict
    computed_at: str
    cache_hit: bool
    error: str | None
    eval: dict | None
    breakdown: list[dict] | None


@router.get("/{dashboard_id}/cards", response_model=list[KpiDashboardCardResponse])
async def dashboard_cards(
    dashboard_id: uuid.UUID,
    force: bool = False,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await get_or_404(db, KpiDashboard, current.org_id, dashboard_id, "Dashboard not found")
    kpi_ids = [uuid.UUID(k) for k in dashboard.kpi_ids]
    if not kpi_ids:
        return []

    kpi_defs = {
        k.id: k
        for k in (await db.execute(select(Kpi).where(Kpi.org_id == current.org_id, Kpi.id.in_(kpi_ids)))).scalars().all()
    }
    items = await kpi_service.compute_kpis_multi_dataset(
        db, current.org_id, kpi_ids, force=force, include_eval=True, lookback=14,
    )

    out: list[KpiDashboardCardResponse] = []
    for item in items:
        kdef = kpi_defs.get(item.kpi_id)
        out.append(
            KpiDashboardCardResponse(
                kpi_id=str(item.kpi_id),
                dataset_id=str(kdef.dataset_id) if kdef else "",
                name=kdef.name if kdef else "Unknown KPI",
                unit=kdef.unit if kdef else None,
                direction=kdef.direction if kdef else "neutral",
                value=item.value, meta=item.meta, computed_at=item.computed_at.isoformat(),
                cache_hit=item.cache_hit, error=item.error, eval=item.eval,
                breakdown=[{"label": l, "value": v} for l, v in item.breakdown] if item.breakdown else None,
            )
        )
    return out
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, change the import block:

```python
    items,
    kpis,
```
to:
```python
    items,
    kpi_dashboards,
    kpis,
```

And add, right after `app.include_router(kpis.router)`:

```python
app.include_router(kpi_dashboards.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `backend/`): `pytest tests/test_kpi_dashboards.py -v`
Expected: PASS — all 3 tests

Run: `pytest tests/test_kpis.py tests/test_kpi_dashboards.py -v`
Expected: PASS — full regression check across both files

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/kpi_dashboards.py backend/app/main.py backend/tests/test_kpi_dashboards.py
git commit -m "feat: add KPI dashboards CRUD + multi-dataset cards endpoint"
```

---

### Task 7: `Sparkline` and `BreakdownBars` components

**Files:**
- Create: `frontend/src/components/Sparkline.tsx`
- Create: `frontend/src/components/BreakdownBars.tsx`

**Interfaces:**
- Produces: `Sparkline({ values, width?, height? })`, `BreakdownBars({ items: { label: string; value: number }[] })` — consumed by Task 9 (`KpiDashboards.tsx`).

- [ ] **Step 1: Write `Sparkline`**

Create `frontend/src/components/Sparkline.tsx`:

```tsx
export function Sparkline({ values, width = 96, height = 28 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible text-primary">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
```

- [ ] **Step 2: Write `BreakdownBars`**

Create `frontend/src/components/BreakdownBars.tsx`:

```tsx
export function BreakdownBars({ items }: { items: { label: string; value: number }[] }) {
  if (items.length === 0) return null;
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1);
  return (
    <div className="space-y-1.5">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2 text-xs">
          <span className="w-24 shrink-0 truncate text-muted-foreground" title={item.label}>
            {item.label}
          </span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-primary/70"
              style={{ width: `${(Math.abs(item.value) / max) * 100}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right tabular-nums text-foreground">
            {item.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}
          </span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Verify they typecheck**

Run (from `frontend/`): `npx tsc --noEmit -p .`
Expected: no errors referencing `Sparkline.tsx` or `BreakdownBars.tsx`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sparkline.tsx frontend/src/components/BreakdownBars.tsx
git commit -m "feat: add Sparkline and BreakdownBars display components"
```

---

### Task 8: Breakdown picker in `Kpis.tsx` form + Dashboards nav link

**Files:**
- Modify: `frontend/src/pages/Kpis.tsx`

**Interfaces:**
- Consumes: `datasets.data[].columns: {name, dtype}[]` (existing, from `useDatasets()`), `POST /kpis` now accepting `breakdown_group_col/breakdown_agg/breakdown_value_col` (Task 3).

- [ ] **Step 1: Add the navigate import and button**

In `frontend/src/pages/Kpis.tsx`, add to the top imports:

```tsx
import { useNavigate } from "react-router-dom";
```

Inside `export function Kpis()`, right after `const datasets = useDatasets();`, add:

```tsx
  const navigate = useNavigate();
```

In the `PageHeader` `actions` prop, add a button as the first child of the `<div className="flex gap-2">`:

```tsx
            <Button variant="ghost" onClick={() => navigate("/kpis/dashboards")}>
              Dashboards
            </Button>
```

- [ ] **Step 2: Extend `emptyForm()`**

Replace:

```tsx
function emptyForm() {
  return {
    name: "",
    description: "",
    dataset_id: "",
    sql: "SELECT AVG(TRY_CAST(value AS DOUBLE)) FROM {table}",
    unit: "",
    direction: "neutral" as KpiDef["direction"],
    tags: "",
  };
}
```

with:

```tsx
function emptyForm() {
  return {
    name: "",
    description: "",
    dataset_id: "",
    sql: "SELECT AVG(TRY_CAST(value AS DOUBLE)) FROM {table}",
    unit: "",
    direction: "neutral" as KpiDef["direction"],
    tags: "",
    breakdownGroupCol: "",
    breakdownAgg: "count" as "count" | "avg" | "sum",
    breakdownValueCol: "",
  };
}
```

- [ ] **Step 3: Add a `breakdownColumns` memo**

Add near the top imports:

```tsx
import { useMemo } from "react";
```

(Change `import { useState } from "react";` to `import { useMemo, useState } from "react";`)

Inside the component, after `const datasetName = ...` line, add:

```tsx
  const breakdownColumns = useMemo(
    () => datasets.data?.find((d) => d.id === form.dataset_id)?.columns.map((c) => c.name) ?? [],
    [datasets.data, form.dataset_id],
  );
```

- [ ] **Step 4: Include breakdown fields in the create mutation body**

In the `create` mutation's `mutationFn`, replace the `body: JSON.stringify({...})` block:

```tsx
        body: JSON.stringify({
          name: form.name,
          description: form.description || null,
          dataset_id: form.dataset_id,
          sql: form.sql,
          unit: form.unit || null,
          direction: form.direction,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        }),
```

with:

```tsx
        body: JSON.stringify({
          name: form.name,
          description: form.description || null,
          dataset_id: form.dataset_id,
          sql: form.sql,
          unit: form.unit || null,
          direction: form.direction,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
          breakdown_group_col: form.breakdownGroupCol || null,
          breakdown_agg: form.breakdownGroupCol ? form.breakdownAgg : null,
          breakdown_value_col: form.breakdownAgg !== "count" ? form.breakdownValueCol || null : null,
        }),
```

- [ ] **Step 5: Add the picker UI to the form**

In the `<Dialog>`'s `<form>`, after the `Tags` `<Field>` block and before the submit-buttons `<div>`, add:

```tsx
          <Field label="Breakdown (optional)" hint="Group this KPI by a column — per-machine averages, or a Pareto of a reason column.">
            <div className="grid grid-cols-3 gap-2">
              <Select
                value={form.breakdownGroupCol}
                onChange={(e) => setForm((f) => ({ ...f, breakdownGroupCol: e.target.value }))}
              >
                <option value="">No breakdown</option>
                {breakdownColumns.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </Select>
              <Select
                value={form.breakdownAgg}
                onChange={(e) => setForm((f) => ({ ...f, breakdownAgg: e.target.value as typeof form.breakdownAgg }))}
                disabled={!form.breakdownGroupCol}
              >
                <option value="count">Count rows</option>
                <option value="avg">Average of…</option>
                <option value="sum">Sum of…</option>
              </Select>
              <Select
                value={form.breakdownValueCol}
                onChange={(e) => setForm((f) => ({ ...f, breakdownValueCol: e.target.value }))}
                disabled={!form.breakdownGroupCol || form.breakdownAgg === "count"}
              >
                <option value="">Select column…</option>
                {breakdownColumns.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </Select>
            </div>
          </Field>
```

- [ ] **Step 6: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit -p .`
Expected: no errors in `Kpis.tsx`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Kpis.tsx
git commit -m "feat: add breakdown column picker to the KPI creation form"
```

---

### Task 9: `KpiDashboards.tsx` page + routing

**Files:**
- Create: `frontend/src/pages/KpiDashboards.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/lib/nav.ts`

**Interfaces:**
- Consumes: `GET/POST /kpi-dashboards`, `PATCH/DELETE /kpi-dashboards/{id}`, `GET /kpi-dashboards/{id}/cards` (Task 6), `GET /kpis` (existing), `GET /kpis/{id}/history?dataset_id=...&limit=...` (existing), `Sparkline`, `BreakdownBars` (Task 7).

- [ ] **Step 1: Write the page**

Create `frontend/src/pages/KpiDashboards.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Maximize2, Minimize2, Rows3 } from "lucide-react";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/Sparkline";
import { BreakdownBars } from "@/components/BreakdownBars";

interface KpiDashboardDef {
  id: string;
  name: string;
  tier: "operator" | "supervisor";
  kpi_ids: string[];
  refresh_seconds: number;
}

interface KpiDef {
  id: string;
  name: string;
}

interface DashboardCard {
  kpi_id: string;
  dataset_id: string;
  name: string;
  unit: string | null;
  direction: string;
  value: number | null;
  error: string | null;
  computed_at: string;
  eval: { status: "ok" | "warn" | "breach" | "unknown"; trend: string } | null;
  breakdown: { label: string; value: number }[] | null;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  ok: "success",
  warn: "warning",
  breach: "danger",
  unknown: "neutral",
};

const TREND_ARROW: Record<string, string> = { up: "↑", down: "↓", flat: "→", unknown: "" };

function useKpiHistory(kpiId: string, datasetId: string) {
  return useQuery<{ value: number | null }[]>({
    queryKey: ["kpi-history", kpiId, datasetId],
    queryFn: async () => {
      const res = await apiFetch(`/kpis/${kpiId}/history?dataset_id=${datasetId}&limit=20`);
      if (!res.ok) throw new Error("Failed to load history");
      return res.json();
    },
    enabled: Boolean(kpiId && datasetId),
  });
}

function KpiCard({ card }: { card: DashboardCard }) {
  const history = useKpiHistory(card.kpi_id, card.dataset_id);
  const sparkValues = useMemo(
    () => (history.data ?? []).map((h) => h.value).filter((v): v is number => v !== null).reverse(),
    [history.data],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
          {card.name}
          {card.eval && <Badge variant={STATUS_VARIANT[card.eval.status]}>{card.eval.status}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {card.error ? (
          <Badge variant="danger">{card.error}</Badge>
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums">{card.value ?? "—"}</span>
              {card.unit && <span className="text-sm text-muted-foreground">{card.unit}</span>}
              {card.eval && <span className="text-lg text-muted-foreground">{TREND_ARROW[card.eval.trend]}</span>}
            </div>
            {sparkValues.length >= 2 && <Sparkline values={sparkValues} />}
            {card.breakdown && <BreakdownBars items={card.breakdown} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function KpiDashboards() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", tier: "supervisor" as "operator" | "supervisor", kpi_ids: [] as string[] });
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);

  const dashboards = useQuery<KpiDashboardDef[]>({
    queryKey: ["kpi-dashboards"],
    queryFn: async () => {
      const res = await apiFetch("/kpi-dashboards");
      if (!res.ok) throw new Error("Failed to load dashboards");
      return res.json();
    },
  });

  const kpis = useQuery<KpiDef[]>({
    queryKey: ["kpis"],
    queryFn: async () => {
      const res = await apiFetch("/kpis");
      if (!res.ok) throw new Error("Failed to load KPIs");
      return res.json();
    },
  });

  const active = dashboards.data?.find((d) => d.id === selectedId) ?? dashboards.data?.[0];

  const cards = useQuery<DashboardCard[]>({
    queryKey: ["kpi-dashboard-cards", active?.id],
    queryFn: async () => {
      const res = await apiFetch(`/kpi-dashboards/${active!.id}/cards`);
      if (!res.ok) throw new Error("Failed to compute dashboard");
      return res.json();
    },
    enabled: Boolean(active?.id),
    // ponytail: this is a GET, so React Query's own refetchInterval covers
    // auto-refresh — no need to hand-roll a setInterval the way the
    // mutation-based EngineScreen "compute" pattern has to.
    refetchInterval: autoRefresh && active ? active.refresh_seconds * 1000 : false,
  });

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/kpi-dashboards", {
        method: "POST",
        body: JSON.stringify({ name: form.name, tier: form.tier, kpi_ids: form.kpi_ids }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create dashboard");
      return res.json() as Promise<KpiDashboardDef>;
    },
    onSuccess: (d) => {
      setCreateOpen(false);
      setForm({ name: "", tier: "supervisor", kpi_ids: [] });
      qc.invalidateQueries({ queryKey: ["kpi-dashboards"] });
      setSelectedId(d.id);
    },
  });

  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else gridRef.current?.requestFullscreen();
  }

  function toggleKpi(id: string) {
    setForm((f) => ({
      ...f,
      kpi_ids: f.kpi_ids.includes(id) ? f.kpi_ids.filter((k) => k !== id) : [...f.kpi_ids, id],
    }));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="KPI Dashboards"
        description="Live, auto-refreshing views of your KPIs — tiered for operators (fast refresh, fullscreen) or supervisors (slower refresh, full detail)."
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => navigate("/kpis")}>Definitions</Button>
            {dashboards.data && dashboards.data.length > 0 && (
              <Select value={active?.id ?? ""} onChange={(e) => setSelectedId(e.target.value)} className="w-56">
                {dashboards.data.map((d) => (
                  <option key={d.id} value={d.id}>{d.name} ({d.tier})</option>
                ))}
              </Select>
            )}
            <Button onClick={() => setCreateOpen(true)}>New dashboard</Button>
          </div>
        }
      />

      {dashboards.data && dashboards.data.length === 0 ? (
        <EmptyState
          icon={<Rows3 />}
          title="No dashboards yet"
          description="Create a named, ordered collection of KPIs — tag it operator for a fullscreen shop-floor tile view, or supervisor for the full card grid."
        />
      ) : active ? (
        <>
          <div className="flex items-center justify-end gap-4">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh ({active.refresh_seconds}s)
            </label>
            <Button variant="ghost" size="sm" onClick={toggleFullscreen}>
              {isFullscreen ? <Minimize2 /> : <Maximize2 />}
              {isFullscreen ? "Exit TV mode" : "TV mode"}
            </Button>
          </div>
          <div
            ref={gridRef}
            className={
              isFullscreen
                ? "grid gap-4 bg-background p-8 sm:grid-cols-2 lg:grid-cols-3"
                : "grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
            }
          >
            {cards.data?.map((card) => (
              <KpiCard key={card.kpi_id} card={card} />
            ))}
          </div>
        </>
      ) : null}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} title="New dashboard">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          </Field>
          <Field label="Tier" hint="Operator defaults to a fast 15s refresh; supervisor to 300s.">
            <Select
              value={form.tier}
              onChange={(e) => setForm((f) => ({ ...f, tier: e.target.value as "operator" | "supervisor" }))}
            >
              <option value="supervisor">Supervisor</option>
              <option value="operator">Operator</option>
            </Select>
          </Field>
          <Field label="KPIs">
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-input p-2">
              {kpis.data?.map((k) => (
                <label key={k.id} className="flex items-center gap-2 text-sm">
                  <Checkbox checked={form.kpi_ids.includes(k.id)} onChange={() => toggleKpi(k.id)} />
                  {k.name}
                </label>
              ))}
            </div>
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending} disabled={form.kpi_ids.length === 0}>Create</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: Add the route**

In `frontend/src/App.tsx`, add the import right after `import { Kpis } from "./pages/Kpis";`:

```tsx
import { KpiDashboards } from "./pages/KpiDashboards";
```

Add the route right after `<Route path="/kpis" element={<Kpis />} />`:

```tsx
        <Route path="/kpis/dashboards" element={<KpiDashboards />} />
```

- [ ] **Step 3: Add the nav entry**

In `frontend/src/lib/nav.ts`, add `Rows3` to the lucide-react import list (alphabetically, between `Radio` and `Search`):

```ts
  Radio,
  Rows3,
  Search,
```

Add the nav item right after the KPIs entry:

```ts
      { to: "/kpis", label: "KPIs", icon: Target },
      { to: "/kpis/dashboards", label: "KPI Dashboards", icon: Rows3 },
```

- [ ] **Step 4: Typecheck and build**

Run (from `frontend/`): `npm run build`
Expected: exits 0, no TypeScript errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/KpiDashboards.tsx frontend/src/App.tsx frontend/src/lib/nav.ts
git commit -m "feat: add tiered, auto-refreshing KPI Dashboards page"
```

---

### Task 10: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Start backend and frontend dev servers**

From `backend/`: `python -m uvicorn app.main:app --reload`
From `frontend/`: `npm run dev`

- [ ] **Step 2: Verify the breakdown picker end-to-end**

In the browser: log in, go to KPIs, upload a CSV with a categorical column (e.g. `machine_id,scrap_units\nM1,5\nM2,3\nM1,2`), create a KPI with SQL `SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}`, set Breakdown to group by `machine_id` / Average of `scrap_units`. Confirm the KPI is created without error and click Compute — confirm the row shows a value (no error badge).

- [ ] **Step 3: Verify the dashboard end-to-end**

Click "Dashboards" from the KPIs page. Create a new dashboard, tier "supervisor", select the KPI from Step 2. Confirm a card renders with the KPI's name, value, and a `BreakdownBars` chart underneath showing `M1`/`M2` bars. Toggle "Auto-refresh" off and on — confirm no console errors. Click "TV mode" — confirm the grid goes fullscreen and "Exit TV mode" returns it to normal.

- [ ] **Step 4: Verify RBAC**

Using a second, non-owner-role browser session (or `curl` with a member-role token from the test setup), confirm `POST /kpis` and `POST /kpi-dashboards` return 403, while `GET /kpis` and `GET /kpi-dashboards` still return 200.

- [ ] **Step 5: Run the full backend test suite**

Run (from `backend/`): `pytest -v`
Expected: all tests pass, including every test added in Tasks 2, 3, and 6
