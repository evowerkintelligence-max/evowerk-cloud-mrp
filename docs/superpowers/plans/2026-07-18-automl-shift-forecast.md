# AutoML Shift Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-shift "Forecast" card to the Shifts analytics page that projects that shift's primary metric forward, by materializing its already-computed day-bucketed trend into a small Dataset and driving the *existing*, unmodified AutoML forecasting engine end-to-end.

**Architecture:** One new table (`ShiftForecastDataset`, a per-`(org, shift)` copy of the existing `DowntimeLiveDataset` materialization pattern) plus one new endpoint (`POST /analytics/shifts/forecast-source`) that reuses `shifts_service.analyze_shifts`'s already-computed `trend` array to build a `period,value` CSV and create-or-overwrite that shift's Dataset. Everything downstream — training, polling, trajectory — goes through the AutoML module's *existing* endpoints and frontend hooks unmodified.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + DuckDB (backend), React + TanStack Query + Tailwind + ECharts (frontend). Same libraries already used by `shifts`, `downtime_tracking`, and `automl` — no new dependencies.

**Full design context:** `docs/superpowers/specs/2026-07-18-automl-shift-forecast-design.md`

## Global Constraints

- Every new/modified backend model, service function, and route follows the exact patterns in `backend/app/models/downtime_tracking.py`, `backend/app/services/downtime_tracking.py`, and `backend/app/api/routes/downtime_tracking.py` — org-scoped queries, `CurrentUser`/`get_current_user` for auth, `ds_service.resolve_dataset`/`ds_service.validate_columns` for dataset access.
- Service functions `flush()` but never `commit()` — the router commits once, matching every existing route in this codebase.
- `pragma: allowlist secret` stays on the migration's `revision`/`down_revision` literals (matches every existing migration file's style — these aren't real secrets, it's a standing false-positive suppression).
- No new third-party dependencies. No changes to `backend/app/services/automl_engine.py` — this feature only feeds the engine a Dataset, it doesn't touch how forecasting works.
- New table must be registered in `backend/tests/conftest.py`'s model-import list (drives `Base.metadata.create_all()` for the in-memory test DB).
- Frontend has no automated test runner — the frontend task ends with `tsc -b` typecheck + a manual in-browser verification pass, not automated tests (matches the precedent in `docs/superpowers/specs/2026-07-15-pace-tracking-design.md`).

---

### Task 1: Data model + migration

**Files:**
- Create: `backend/app/models/shift_forecast.py`
- Create: `backend/alembic/versions/b6d3f8a17c92_shift_forecast_datasets.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_shift_forecast.py` (new file)

**Interfaces:**
- Consumes: `app.db.base.Base` (existing declarative base), `app.models.dataset.Dataset` (existing).
- Produces: `ShiftForecastDataset(org_id: uuid.UUID, shift: str, dataset_id: uuid.UUID)` — composite primary key `(org_id, shift)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_shift_forecast.py`:

```python
# backend/tests/test_shift_forecast.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "sf@example.com", org_name: str = "SfOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def test_shift_forecast_dataset_model_roundtrip(client, test_engine):
    from app.models.dataset import Dataset
    from app.models.shift_forecast import ShiftForecastDataset

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = Dataset(
            org_id=org_id,
            name="Shift Forecast Source: A",
            original_filename="shift_forecast.csv",
            fmt="csv",
            storage_ext="csv",
            columns=[{"name": "period", "dtype": "VARCHAR"}, {"name": "value", "dtype": "DOUBLE"}],
            row_count=1,
            size_bytes=10,
            created_by=user_id,
        )
        db.add(dataset)
        await db.flush()

        mapping = ShiftForecastDataset(org_id=org_id, shift="A", dataset_id=dataset.id)
        db.add(mapping)
        await db.commit()
        await db.refresh(mapping)

        assert mapping.org_id == org_id
        assert mapping.shift == "A"
        assert mapping.dataset_id == dataset.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_shift_forecast.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.shift_forecast'`

- [ ] **Step 3: Create the model file**

Create `backend/app/models/shift_forecast.py`:

```python
# backend/app/models/shift_forecast.py
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShiftForecastDataset(Base):
    """One row per (org, shift) - points at the Dataset that
    app.services.shifts.prepare_forecast_source keeps overwriting with that
    shift's latest day-bucketed trend series, mirroring DowntimeLiveDataset's
    one-dataset-per-key materialization pattern.
    """

    __tablename__ = "shift_forecast_datasets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    shift: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
```

- [ ] **Step 4: Register the model module in conftest.py so its tables get created**

In `backend/tests/conftest.py`, the import block currently reads (around line 44-46):

```python
    report_spec,
    scenario,
    shift_schedule,
```

Change it to insert the new module alphabetically:

```python
    report_spec,
    scenario,
    shift_forecast,
    shift_schedule,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_shift_forecast.py -v`
Expected: PASS (conftest's `Base.metadata.create_all` creates the table from the model definition; the Alembic migration below is for the real dev/prod database, not exercised by pytest).

- [ ] **Step 6: Create the Alembic migration for the real database**

Run `cd backend && python -m alembic heads` first and confirm the single head is still `f1a5c8d3e942` (if a different migration landed on `master` since this plan was written, use that revision id as `down_revision` instead).

Create `backend/alembic/versions/b6d3f8a17c92_shift_forecast_datasets.py`:

```python
# backend/alembic/versions/b6d3f8a17c92_shift_forecast_datasets.py
"""shift forecast source datasets

Revision ID: b6d3f8a17c92
Revises: f1a5c8d3e942
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d3f8a17c92"  # pragma: allowlist secret
down_revision = "f1a5c8d3e942"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_forecast_datasets",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("shift", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id", "shift"),
    )


def downgrade() -> None:
    op.drop_table("shift_forecast_datasets")
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/shift_forecast.py backend/alembic/versions/b6d3f8a17c92_shift_forecast_datasets.py backend/tests/conftest.py backend/tests/test_shift_forecast.py
git commit -m "feat: add shift-forecast-dataset data model and migration"
```

---

### Task 2: Backend service + route — forecast-source endpoint

**Files:**
- Modify: `backend/app/services/shifts.py`
- Modify: `backend/app/api/routes/shifts.py`
- Test: `backend/tests/test_shift_forecast.py` (append)

**Interfaces:**
- Consumes: `app.services.shifts.analyze_shifts` (existing, unmodified), `app.services.shifts._primary_key` (existing, unmodified), `app.services.datasets.create_dataset` / `overwrite_dataset_file` / `resolve_dataset` / `validate_columns` (all existing), `app.models.shift_forecast.ShiftForecastDataset` (Task 1).
- Produces:
  - `app.services.shifts.prepare_forecast_source(db: AsyncSession, dataset: Dataset, req: Any, *, shift: str, org_id: uuid.UUID, created_by: uuid.UUID) -> dict` (async) returning `{"dataset_id": uuid.UUID, "target_column": "value", "time_column": "period", "metric_label": str, "rows": int}`.
  - `POST /analytics/shifts/forecast-source` — request body: `ShiftAnalyzeRequest` fields plus `shift: str`; response: `{dataset_id: str, target_column: str, time_column: str, metric_label: str, rows: int}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_shift_forecast.py`:

```python
SHIFT_TREND_CSV = (
    b"date,shift,units\n"
    b"2026-07-01,A,100\n"
    b"2026-07-02,A,110\n"
    b"2026-07-03,A,90\n"
    b"2026-07-01,B,50\n"
    b"2026-07-02,B,60\n"
)


async def _register_and_login(client, email: str, org_name: str = "SfOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_trend_dataset(client, headers) -> str:
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("trend.csv", SHIFT_TREND_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_forecast_source_builds_period_value_dataset(client):
    token = await _register_and_login(client, "sfsource@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
            "timestamp_col": "date", "shift": "A",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["target_column"] == "value"
    assert body["time_column"] == "period"
    assert body["metric_label"] == "Units"
    assert body["rows"] == 3

    ds = await client.get(f"/datasets/{body['dataset_id']}", headers=headers)
    assert ds.status_code == 200
    cols = {c["name"] for c in ds.json()["columns"]}
    assert cols == {"period", "value"}
    assert ds.json()["row_count"] == 3


async def test_forecast_source_reuses_same_dataset_on_repeat_calls(client):
    token = await _register_and_login(client, "sfreuse@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)
    body = {
        "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
        "timestamp_col": "date", "shift": "A",
    }

    first = await client.post("/analytics/shifts/forecast-source", headers=headers, json=body)
    second = await client.post("/analytics/shifts/forecast-source", headers=headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["dataset_id"] == second.json()["dataset_id"]


async def test_forecast_source_requires_timestamp_column(client):
    token = await _register_and_login(client, "sfnots@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={"dataset_id": dataset_id, "shift_col": "shift", "units_col": "units", "shift": "A"},
    )
    assert res.status_code == 400
    assert "timestamp" in res.json()["detail"].lower()


async def test_forecast_source_400_for_shift_with_no_trend_rows(client):
    token = await _register_and_login(client, "sfnoshift@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_trend_dataset(client, headers)

    res = await client.post(
        "/analytics/shifts/forecast-source", headers=headers,
        json={
            "dataset_id": dataset_id, "shift_col": "shift", "units_col": "units",
            "timestamp_col": "date", "shift": "C",
        },
    )
    assert res.status_code == 400
    assert "no trend data" in res.json()["detail"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_shift_forecast.py -v`
Expected: the four new tests FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add `prepare_forecast_source` to the service**

In `backend/app/services/shifts.py`, add these imports at the top (alongside the existing ones):

```python
import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shift_forecast import ShiftForecastDataset
```

Then append this function at the end of the file:

```python
_METRIC_LABELS = {
    "oee_pct": "OEE",
    "fpq_pct": "FPQ",
    "total_units": "Units",
    "total_downtime_min": "Downtime (min)",
}


async def prepare_forecast_source(
    db: AsyncSession,
    dataset: Dataset,
    req: Any,
    *,
    shift: str,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
) -> dict:
    """Materialize one shift's day-bucketed trend (the same series the "Shift
    trend" chart already renders) into a small two-column Dataset an AutoML
    forecasting run can train on - overwriting the same Dataset on repeat
    calls for a given (org, shift), mirroring downtime_tracking._materialize.
    """
    if not req.timestamp_col:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Map a timestamp column to enable forecasting.")

    analysis = analyze_shifts(dataset, req)
    points = sorted(
        (t for t in analysis["trend"] if t["shift"] == shift and t["value"] is not None),
        key=lambda t: t["period"],
    )
    if not points:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"No trend data for shift '{shift}' to forecast from."
        )

    metrics = analysis["metrics_available"]
    key, _ = _primary_key(
        has_oee="oee" in metrics, has_scrap="scrap" in metrics,
        has_throughput="throughput" in metrics, has_downtime="downtime" in metrics,
    )
    metric_label = _METRIC_LABELS.get(key or "", "Value")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["period", "value"])
    for p in points:
        writer.writerow([p["period"], p["value"]])
    csv_bytes = buf.getvalue().encode("utf-8")

    mapping = (
        await db.execute(
            select(ShiftForecastDataset).where(
                ShiftForecastDataset.org_id == org_id, ShiftForecastDataset.shift == shift
            )
        )
    ).scalar_one_or_none()

    if mapping is None:
        forecast_dataset = await ds_service.create_dataset(
            db, org_id=org_id, created_by=created_by,
            name=f"Shift Forecast Source: {shift}", filename="shift_forecast.csv", data=csv_bytes,
        )
        db.add(ShiftForecastDataset(org_id=org_id, shift=shift, dataset_id=forecast_dataset.id))
        await db.flush()
        dataset_id = forecast_dataset.id
    else:
        forecast_dataset = await ds_service.resolve_dataset(db, org_id, str(mapping.dataset_id))
        await ds_service.overwrite_dataset_file(db, forecast_dataset, data=csv_bytes)
        dataset_id = forecast_dataset.id

    return {
        "dataset_id": dataset_id,
        "target_column": "value",
        "time_column": "period",
        "metric_label": metric_label,
        "rows": len(points),
    }
```

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/shifts.py`, find:

```python
    return ShiftAnalyzeResponse(**shifts_service.analyze_shifts(dataset, req))


class ShiftScheduleItem(BaseModel):
```

Replace with (inserting the new request/response models and route between the two):

```python
    return ShiftAnalyzeResponse(**shifts_service.analyze_shifts(dataset, req))


class ShiftForecastSourceRequest(ShiftAnalyzeRequest):
    shift: str = Field(..., min_length=1)


class ShiftForecastSourceResponse(BaseModel):
    dataset_id: str
    target_column: str
    time_column: str
    metric_label: str
    rows: int


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
    return ShiftForecastSourceResponse(
        dataset_id=str(result["dataset_id"]),
        target_column=result["target_column"],
        time_column=result["time_column"],
        metric_label=result["metric_label"],
        rows=result["rows"],
    )


class ShiftScheduleItem(BaseModel):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_shift_forecast.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Run the full backend test suite to check for regressions**

Run: `cd backend && python -m pytest -q`
Expected: no new failures (pre-existing skip-marked realtime tests are unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/shifts.py backend/app/api/routes/shifts.py backend/tests/test_shift_forecast.py
git commit -m "feat: add /analytics/shifts/forecast-source endpoint"
```

---

### Task 3: Frontend — Forecast card on the Shifts page

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx`
- Modify: `frontend/src/pages/analytics/Shifts.tsx`

**Interfaces:**
- Consumes: existing `frontend/src/pages/automl/api.ts` exports `useAutoMLRuns`, `useAutoMLRun`, `useCreateAutoMLRun`, `useForecastTrajectory`, `type AutoMLRunSummary`, `type ForecastTrajectoryResult` (all unmodified); existing `EngineScreen` from `shared.tsx` (widened in Step 1 below); new backend endpoint `POST /analytics/shifts/forecast-source` from Task 2.
- Produces: `ShiftForecastCard` component (local to `Shifts.tsx`, not exported elsewhere — no other page needs it).

- [ ] **Step 1: Widen `EngineScreen`'s `children` prop to also pass the last request body**

In `frontend/src/pages/analytics/shared.tsx`, find:

```tsx
  children: (result: T) => ReactNode;
```

Replace with:

```tsx
  children: (result: T, requestBody: Record<string, unknown>) => ReactNode;
```

Then find:

```tsx
          {children(result)}
```

Replace with:

```tsx
          {children(result, lastComputeBody.current ?? {})}
```

This is additive and backward-compatible — every other page's `children={(r) => ...}` callback still type-checks (TypeScript allows passing a function with fewer parameters where more are expected).

- [ ] **Step 2: Typecheck to confirm no other page broke**

Run: `cd frontend && npx tsc -b`
Expected: no new errors. (If some other `EngineScreen` consumer somehow fails, stop and investigate before continuing — it means an assumption here was wrong.)

- [ ] **Step 3: Add the forecast hook and card component**

In `frontend/src/pages/analytics/Shifts.tsx`, change the top imports:

```tsx
import { useState } from "react";
```

to:

```tsx
import { useEffect, useState } from "react";
```

and add a new import block right after the existing `"./shared"` import:

```tsx
import {
  useAutoMLRun,
  useAutoMLRuns,
  useCreateAutoMLRun,
  useForecastTrajectory,
} from "../automl/api";
```

Then, right before `export function Shifts() {`, add:

```tsx
interface ForecastSource {
  dataset_id: string;
  target_column: string;
  time_column: string;
  metric_label: string;
  rows: number;
}

// N_LAGS + 1 in backend/app/services/automl_engine.py's recursive trajectory
// walk - the minimum history a forecast run needs before it can train at all.
const MIN_FORECAST_ROWS = 15;

function useShiftForecastSource(shift: string, requestBody: Record<string, unknown> | null) {
  return useQuery<ForecastSource>({
    queryKey: ["shift-forecast-source", shift, requestBody],
    queryFn: async () => {
      const res = await apiFetch("/analytics/shifts/forecast-source", {
        method: "POST",
        body: JSON.stringify({ ...requestBody, shift }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to prepare forecast data");
      return res.json();
    },
    enabled: !!requestBody && !!shift,
  });
}

// Single-shift forecast card - materializes the shift's trend into a Dataset
// via forecast-source, then trains/polls/renders through the *existing*
// AutoML endpoints and hooks (useCreateAutoMLRun/useAutoMLRun/
// useForecastTrajectory), same as the AutoML page itself uses.
function ShiftForecastCard({ shift, requestBody }: { shift: string; requestBody: Record<string, unknown> | null }) {
  const source = useShiftForecastSource(shift, requestBody);
  const runs = useAutoMLRuns();
  const [runId, setRunId] = useState<string | null>(null);

  useEffect(() => {
    setRunId(null);
  }, [shift]);

  const existingRun = source.data
    ? [...(runs.data ?? [])]
        .filter((r) => r.dataset_id === source.data!.dataset_id && r.task === "forecasting")
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
    : undefined;
  const activeRunId = runId ?? existingRun?.id;

  const run = useAutoMLRun(activeRunId);
  const trainMut = useCreateAutoMLRun();
  const trajectory = useForecastTrajectory(activeRunId, run.data?.status === "success");

  function train() {
    if (!source.data) return;
    trainMut.mutate(
      {
        name: `Shift forecast: ${shift}`,
        dataset_id: source.data.dataset_id,
        task: "forecasting",
        target_column: source.data.target_column,
        feature_columns: [],
        thoroughness: "fast",
        algorithms: null,
        handle_class_imbalance: true,
        time_column: source.data.time_column,
        horizon: 14,
        forecast_strategy: "recursive",
        time_budget_seconds: null,
        search_method: "random",
      },
      { onSuccess: (r) => setRunId(r.id) },
    );
  }

  const isTraining = trainMut.isPending || run.data?.status === "queued" || run.data?.status === "running";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Forecast — {shift}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {source.isLoading && <p className="text-sm text-muted-foreground">Preparing forecast data…</p>}
        {source.isError && <p className="text-sm text-destructive">{(source.error as Error).message}</p>}
        {source.data && (
          <>
            <p className="text-xs text-muted-foreground">
              {source.data.metric_label} · {source.data.rows} day(s) of history
              {source.data.rows < MIN_FORECAST_ROWS &&
                ` — forecasting needs at least ${MIN_FORECAST_ROWS} days of history for this shift.`}
            </p>
            <Button size="sm" loading={isTraining} onClick={train}>
              {activeRunId ? "Retrain forecast" : "Train forecast"}
            </Button>
            {run.data?.status === "failed" && <p className="text-sm text-destructive">{run.data.error}</p>}
            {trajectory.data && (
              <>
                <EChart
                  height={220}
                  option={{
                    tooltip: TOOLTIP,
                    grid: { top: 20, bottom: 40, left: 48, right: 20 },
                    xAxis: {
                      type: "category",
                      data: [
                        ...trajectory.data.history.map((h) => h.time),
                        ...trajectory.data.points.map((p) => p.time),
                      ],
                      axisLabel: { ...AXIS_LABEL, rotate: 30 },
                      axisLine: AXIS_LINE,
                    },
                    yAxis: { type: "value", axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
                    series: [
                      {
                        name: "Actual",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map((h) => h.actual),
                          ...trajectory.data.points.map(() => null),
                        ],
                        lineStyle: { width: 2, color: C.blue },
                      },
                      {
                        name: "Forecast",
                        type: "line" as const,
                        symbol: "none" as const,
                        data: [
                          ...trajectory.data.history.map(() => null),
                          ...trajectory.data.points.map((p) => p.prediction),
                        ],
                        lineStyle: { width: 2, color: C.amber, type: "dashed" as const },
                      },
                    ],
                  }}
                />
                {trajectory.data.notes.map((n, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    {n}
                  </p>
                ))}
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Wire the card into the page**

In `frontend/src/pages/analytics/Shifts.tsx`, find:

```tsx
      {(r) => {
```

Replace with:

```tsx
      {(r, reqBody) => {
```

Then find the end of the "Hourly pace" card block, immediately followed by `<ScheduleEditor shiftLabels={r.shifts.map((s) => s.shift)} />`:

```tsx
            )}

            <ScheduleEditor shiftLabels={r.shifts.map((s) => s.shift)} />
```

Replace with:

```tsx
            )}

            {handoverCandidate && <ShiftForecastCard shift={handoverCandidate} requestBody={reqBody} />}

            <ScheduleEditor shiftLabels={r.shifts.map((s) => s.shift)} />
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 6: Manual in-browser verification**

Start the backend and frontend dev servers, log in, go to Shifts:

1. Upload a CSV with `date,shift,units` columns covering at least 15 distinct dates for one shift (e.g. `A`) and map `shift_col`/`units_col`/`timestamp_col`, then click "Compare shifts".
2. Confirm the new "Forecast — A" card appears below "Hourly pace" and shows `Units · N day(s) of history`.
3. Click "Train forecast"; confirm the button shows a loading state, then a status badge/error surfaces if training fails, or a chart with an "Actual" solid line and a dashed "Forecast" line appears on success.
4. Switch the Handover shift dropdown to a different shift; confirm the card's title and data update to the new shift and don't show the previous shift's stale run.
5. Reload the page; confirm the card re-finds the just-trained run (via the `dataset_id` match against `GET /automl/runs`) instead of requiring retraining.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx frontend/src/pages/analytics/Shifts.tsx
git commit -m "feat: add AutoML-backed forecast card to the Shifts page"
```
