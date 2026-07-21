# Manufacturing AutoML Templates - Plan 1: Template Registry + Predictive Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the config-driven AutoML template registry and prove it end-to-end by re-platforming Predictive Maintenance off its old hardcoded `GradientBoostingClassifier` onto the shared `automl_engine.train_automl_baseline()` bake-off, while preserving 100% of its existing RUL/cost/recommendation math and its statistical fallback for low-data machines.

**Architecture:** A new `app/services/automl_templates/` package declares an `AutoMLTemplate` (roles + `preprocess()` + `postprocess()`) per use case. `automl_runner.py`'s existing background-run executor gains a template branch: when `AutoMLRun.template_id` is set, it calls the template's `preprocess()` to turn the mapped dataset into engine input (or a "not enough signal" sentinel), optionally calls the *unmodified* `automl_engine.train_automl_baseline()`, then calls `postprocess()` to turn the result into the domain shape the existing `Predictive.tsx` page already renders. The old synchronous `POST /datasets/{id}/predictive/analyze` endpoint is removed; predictive maintenance becomes an async run-then-poll flow like every other AutoML template.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, pandas/numpy/scikit-learn (existing `automl_engine.py`, untouched), React Query (frontend polling).

## Global Constraints

- `automl_engine.py`'s `train_automl_baseline()`, `predict_with_model()`, and every other function in that file are **not modified** by this plan - every template calls them, none of them change.
- Every route stays org-scoped via `current.org_id`, matching every existing route in this codebase (no exceptions).
- `AutoMLRun.template_id = None` must continue to mean "generic/Advanced run" - the existing `POST /automl/runs` flow (`tests/test_automl.py`, 7 tests) must keep passing unchanged.
- Predictive maintenance's SVC/LinearSVC/LinearSVR/SVR candidates are excluded from its algorithm roster - none of them expose `.predict_proba()` in `automl_engine._all_models()`'s current configuration (no `probability=True` on `SVC`), and this template needs a probability, not just a class label.
- Test password policy (12+ chars, letter + digit): reuse `"correct-horse-battery-staple1"`, the constant already used in `tests/test_automl.py`/`tests/test_predictive.py`.

---

## File Structure

New:
- `backend/app/services/automl_templates/__init__.py` - `ColumnRole`, `PreprocessResult`, `RecommendationRule`, `AutoMLTemplate` dataclasses + `register_template()`/`get_template()`/`list_templates()` registry functions.
- `backend/app/services/automl_templates/predictive_maintenance.py` - the one template this plan ships: roles, `build_training_frame()` (ported rolling-window featurization), `preprocess()`, `postprocess()`.
- `backend/alembic/versions/<new>_automl_template_id.py` - adds `automl_runs.template_id`.
- `backend/tests/test_automl_templates.py` - registry unit tests + `build_training_frame()` unit tests.
- `backend/tests/test_predictive_maintenance_template.py` - `preprocess()`/`postprocess()` unit tests + end-to-end API tests replacing the two removed `test_predictive.py` cases.

Modified:
- `backend/app/models/automl_run.py` - add `template_id: Mapped[str | None]`.
- `backend/app/services/predictive.py` - extract `_build_daily()` (shared OEE aggregation, used by the new template's `preprocess()`/`postprocess()`); delete `_ml_analyze()` and `analyze()` (superseded); keep dataclasses, `_linreg_slope`/`_std`/`_recent_slope`/`_rul_days`/`_top_driver`/`_stat_analyze`, `_ACTIONS`/`_PM_HOURS`/`_CRITICAL_OEE`/`_WARN_OEE`/`_UNPLANNED_CATS` (all reused by `postprocess()` in this plan); `_ML_FEATURES`/`_ML_FEAT_LABELS` also kept but **not** consumed by anything in this plan (`build_training_frame()` in Task 3 hardcodes the same 6 feature names/labels directly) - they stay for Plan 2's explainability layer, which needs the label mapping; schedule CRUD functions untouched.
- `backend/app/api/routes/predictive.py` - remove `analyze_router` and its request/response models; keep the schedules `router` untouched.
- `backend/app/main.py` - remove `app.include_router(predictive.analyze_router)`.
- `backend/app/services/automl_runner.py` - branch `_execute()` on `run.template_id`.
- `backend/app/api/routes/automl.py` - add `GET /automl/templates`, `POST /automl/templates/{template_id}/runs`.
- `backend/tests/test_predictive.py` - remove `test_analyze_flags_low_oee_machine_high_risk` and `test_analyze_rejects_missing_columns` (moved to `test_predictive_maintenance_template.py` against the new endpoint); keep the 3 schedule tests unchanged.
- `frontend/src/pages/Predictive.tsx` - swap the `analyze` mutation's `mutationFn` to create a template run and poll for completion instead of one synchronous POST; nothing else in the file changes (same `PredictiveResult` shape rendered the same way).

---

### Task 1: Add `template_id` to `AutoMLRun`

**Files:**
- Modify: `backend/app/models/automl_run.py`
- Create: `backend/alembic/versions/<new>_automl_template_id.py`
- Test: `backend/tests/test_automl_templates.py`

**Interfaces:**
- Produces: `AutoMLRun.template_id: str | None` - every later task reads/writes this column.

- [ ] **Step 1: Add the column to the model**

Edit `backend/app/models/automl_run.py`, add right after `dataset_id`:

```python
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
```

- [ ] **Step 2: Determine the current migration head**

Run: `cd backend && python -m alembic heads`

If this errors on missing config (e.g. `MFA_ENCRYPTION_KEY`), set the same test env vars `tests/conftest.py` sets before running it:

```bash
JWT_SECRET_KEY=test-secret-that-is-long-enough-for-hs256 \
MFA_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
python -m alembic heads
```

Expected: one or more revision ids printed. **This repo currently has three unmerged heads** (`a2f7c5e91d84`, `a7c92e4f18d5`, `c8a1e5f93d64` as of 2026-07-17) - by the time this task runs, more may have landed. Use whatever `alembic heads` reports right now, not the ids listed here. If there is still more than one head, chain this migration's `down_revision` off `e2f6a9c14d73` (the `automl_runs` migration) directly rather than off any of the three - this migration only touches `automl_runs`, so it can be grafted onto that specific ancestor without picking a side in the unrelated multi-head situation. Note in the commit message that a future merge migration will need to reconcile the heads (not this plan's job to fix).

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/<new>_automl_template_id.py` (replace `<new>` with a fresh short hex id, e.g. via `python -c "import uuid; print(uuid.uuid4().hex[:12])"`):

```python
"""automl_template_id

Revision ID: <new>
Revises: e2f6a9c14d73
Create Date: 2026-07-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<new>"  # pragma: allowlist secret
down_revision = "e2f6a9c14d73"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automl_runs", sa.Column("template_id", sa.String(length=64), nullable=True))
    op.create_index("ix_automl_runs_template_id", "automl_runs", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_automl_runs_template_id", table_name="automl_runs")
    op.drop_column("automl_runs", "template_id")
```

- [ ] **Step 4: Verify the model and migration agree**

`tests/conftest.py` builds the test schema from `Base.metadata.create_all` (not from running migrations), so the column is already live in tests via Step 1. Confirm the migration file is syntactically valid:

Run: `cd backend && python -c "import ast; ast.parse(open('alembic/versions/<new>_automl_template_id.py').read())"`
Expected: no output (no `SyntaxError`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/automl_run.py backend/alembic/versions/<new>_automl_template_id.py
git commit -m "feat: add template_id column to AutoMLRun"
```

---

### Task 2: Template registry module

**Files:**
- Create: `backend/app/services/automl_templates/__init__.py`
- Test: `backend/tests/test_automl_templates.py`

**Interfaces:**
- Consumes: nothing (pure new module).
- Produces: `ColumnRole(key, label, required=True)`, `PreprocessResult(skip_ml=False, df=None, target_col=None, task=None, feature_cols=None, notes=[], context={})`, `RecommendationRule(driver_pattern, direction, action_text)`, `AutoMLTemplate(id, label, roles, preprocess, postprocess, recommendation_rules=[], is_timeseries=False, min_positive_examples=20)`, `register_template(template)`, `get_template(template_id) -> AutoMLTemplate` (raises `KeyError` if unknown), `list_templates() -> list[AutoMLTemplate]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_automl_templates.py`:

```python
from __future__ import annotations

import pytest

from app.services import automl_templates as templates


def test_register_and_get_template():
    t = templates.AutoMLTemplate(
        id="_test_template",
        label="Test Template",
        roles=[templates.ColumnRole("x_col", "X column")],
        preprocess=lambda df, config: templates.PreprocessResult(),
        postprocess=lambda engine_result, config, context: {},
    )
    templates.register_template(t)
    try:
        fetched = templates.get_template("_test_template")
        assert fetched is t
        assert any(x.id == "_test_template" for x in templates.list_templates())
    finally:
        del templates._REGISTRY["_test_template"]


def test_get_unknown_template_raises():
    with pytest.raises(KeyError, match="Unknown AutoML template"):
        templates.get_template("_does_not_exist")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_templates'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/automl_templates/__init__.py`:

```python
"""Config-driven registry for guided AutoML use-case templates. Each
template declares the column roles its wizard needs, a preprocess() that
turns a mapped Dataset into automl_engine input (or a "not enough signal"
sentinel), and a postprocess() that turns the engine's output into
domain-language results. automl_engine.py itself is never modified by any
template - every template only changes what it's called with.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


@dataclass
class ColumnRole:
    key: str
    label: str
    required: bool = True


@dataclass
class PreprocessResult:
    """What a template's preprocess() hands back to automl_runner. When
    skip_ml=True, automl_runner calls postprocess(None, config, context)
    directly without ever calling automl_engine - used when a template
    determines there isn't enough signal to train (see
    predictive_maintenance.py's statistical fallback)."""
    skip_ml: bool = False
    df: pd.DataFrame | None = None
    target_col: str | None = None
    task: str | None = None  # "classification" | "regression" | "forecasting"
    feature_cols: list[str] | None = None
    algorithms: list[str] | None = None
    notes: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecommendationRule:
    driver_pattern: str
    direction: str  # "high" | "low" | "rising" | "falling"
    action_text: str


@dataclass
class AutoMLTemplate:
    id: str
    label: str
    roles: list[ColumnRole]
    preprocess: Callable[[pd.DataFrame, dict[str, Any]], PreprocessResult]
    postprocess: Callable[[dict[str, Any] | None, dict[str, Any], dict[str, Any]], dict[str, Any]]
    recommendation_rules: list[RecommendationRule] = field(default_factory=list)
    is_timeseries: bool = False
    min_positive_examples: int = 20


_REGISTRY: dict[str, AutoMLTemplate] = {}


def register_template(template: AutoMLTemplate) -> None:
    _REGISTRY[template.id] = template


def get_template(template_id: str) -> AutoMLTemplate:
    if template_id not in _REGISTRY:
        raise KeyError(f"Unknown AutoML template '{template_id}'")
    return _REGISTRY[template_id]


def list_templates() -> list[AutoMLTemplate]:
    return list(_REGISTRY.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/automl_templates/__init__.py backend/tests/test_automl_templates.py
git commit -m "feat: add AutoML template registry"
```

---

### Task 3: Port the rolling-window featurization (`build_training_frame`)

**Files:**
- Create: `backend/app/services/automl_templates/predictive_maintenance.py` (partial - this task only)
- Test: `backend/tests/test_automl_templates.py` (append)

**Interfaces:**
- Consumes: `predictive._linreg_slope` (existing, `backend/app/services/predictive.py:114`).
- Produces: `build_training_frame(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[float]]]`. `daily` must have columns `machine, date, oee, scrap_rate, dt_rate, is_failure` (the exact shape `predictive.py`'s existing `analyze()` already builds at line 400-413). Returns `(training_df, last_row_by_machine)` where `training_df` has columns `oee_7d, oee_slope_7d, scrap_rate_7d, dt_rate_7d, fail_count_7d, days_since_fail, is_failure_next` and `last_row_by_machine` maps machine name to the same 6 feature values (list, in that order) computed on its most recent window - used by `postprocess()` in Task 5.
- Also produces: `has_ml_signal(training_df: pd.DataFrame, min_rows: int = 40) -> bool`.

This is a direct port of `predictive.py._ml_analyze`'s windowing loop (lines 158-188) - same math, same constants, different output shape (a DataFrame instead of numpy arrays fed to a hardcoded model).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_automl_templates.py`:

```python
import pandas as pd

from app.services.automl_templates import predictive_maintenance as pm


def _daily_fixture() -> pd.DataFrame:
    # One machine, 15 days: OEE steady at 0.8 for days 1-10, then a failure
    # on day 11 and degraded OEE (0.3) for days 11-15 - long enough to
    # produce rolling windows (W=7, H=3 -> needs >= 11 days).
    rows = []
    for day in range(1, 16):
        oee = 0.8 if day <= 10 else 0.3
        is_failure = 1 if day == 11 else 0
        rows.append({
            "machine": "M1", "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1),
            "oee": oee, "scrap_rate": 1 - oee, "dt_rate": 1 - oee, "is_failure": is_failure,
        })
    return pd.DataFrame(rows)


def test_build_training_frame_produces_expected_columns_and_row_count():
    daily = _daily_fixture()
    training_df, last_by_machine = pm.build_training_frame(daily)

    # n=15, W=7, H=3 -> range(7, 15-3) = range(7, 12) = 5 rows for M1.
    assert len(training_df) == 5
    assert set(training_df.columns) == {
        "oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d",
        "fail_count_7d", "days_since_fail", "is_failure_next",
    }
    assert "M1" in last_by_machine
    assert len(last_by_machine["M1"]) == 6


def test_build_training_frame_skips_machines_with_too_little_history():
    daily = _daily_fixture()
    short = daily[daily["date"] < pd.Timestamp("2026-01-08")]  # only 7 days: n < W+H+1=11
    training_df, last_by_machine = pm.build_training_frame(short)
    assert len(training_df) == 0
    assert "M1" not in last_by_machine  # n=7 < W=7 is False actually; n>=W so last_x IS set
    # n=7 >= W=7, so last_by_machine IS populated even with no training rows -
    # this machine can still be scored once a model exists, it just didn't
    # contribute training examples itself.


def test_has_ml_signal_requires_min_rows_and_balanced_positive_rate():
    enough_balanced = pd.DataFrame({"is_failure_next": [0, 1] * 25})  # 50 rows, 50% positive
    assert pm.has_ml_signal(enough_balanced) is True

    too_few = pd.DataFrame({"is_failure_next": [0, 1] * 5})  # 10 rows
    assert pm.has_ml_signal(too_few) is False

    all_positive = pd.DataFrame({"is_failure_next": [1] * 50})  # pos_rate=1.0 > 0.90
    assert pm.has_ml_signal(all_positive) is False
```

Fix the second test's contradictory comment/assertion before running it - `n=7 >= W=7` is true, so `last_by_machine` **is** populated for the short fixture. Replace that test with:

```python
def test_build_training_frame_skips_machines_with_too_little_history():
    daily = _daily_fixture()
    short = daily[daily["date"] < pd.Timestamp("2026-01-08")]  # only 7 days: n < W+H+1=11
    training_df, last_by_machine = pm.build_training_frame(short)
    assert len(training_df) == 0  # no full W+H window fits, so zero training rows
    assert "M1" in last_by_machine  # n=7 >= W=7, so a "last window" snapshot still exists
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_templates.predictive_maintenance'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/automl_templates/predictive_maintenance.py`:

```python
"""Predictive Maintenance AutoML template: re-platforms predictive.py's
old hardcoded GradientBoostingClassifier onto the shared automl_engine
bake-off, while keeping its RUL/cost/recommendation math and statistical
fallback for machines with too little history to train on.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import predictive as legacy

_W, _H, _MIN_ROWS = 7, 3, 40


def _days_since(fail_arr: np.ndarray, up_to: int) -> float:
    idxs = np.where(fail_arr[:up_to] == 1)[0]
    return float(up_to - idxs[-1]) if len(idxs) > 0 else 60.0


def build_training_frame(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    """Same rolling-window featurization as the old _ml_analyze, ported to
    produce a flat DataFrame for automl_engine instead of numpy arrays fed
    directly to a hardcoded model. `daily` must have columns machine, date,
    oee, scrap_rate, dt_rate, is_failure (sorted per-machine by date is not
    required - this function sorts internally)."""
    rows: list[dict[str, float]] = []
    last_x: dict[str, list[float]] = {}

    for machine, mdf in daily.groupby("machine"):
        mdf = mdf.sort_values("date").reset_index(drop=True)
        n = len(mdf)
        if n < _W:
            continue

        oee = mdf["oee"].to_numpy(dtype=float)
        scr = mdf["scrap_rate"].to_numpy(dtype=float)
        dtr = mdf["dt_rate"].to_numpy(dtype=float)
        fail = mdf["is_failure"].to_numpy(dtype=int)

        for i in range(_W, n - _H):
            ow = oee[i - _W:i]
            rows.append({
                "oee_7d": float(np.mean(ow)),
                "oee_slope_7d": legacy._linreg_slope(ow.tolist()),
                "scrap_rate_7d": float(np.mean(scr[i - _W:i])),
                "dt_rate_7d": float(np.mean(dtr[i - _W:i])),
                "fail_count_7d": float(np.sum(fail[i - _W:i])),
                "days_since_fail": _days_since(fail, i),
                "is_failure_next": 1 if np.sum(fail[i:i + _H]) > 0 else 0,
            })

        ow = oee[n - _W:n]
        last_x[str(machine)] = [
            float(np.mean(ow)), legacy._linreg_slope(ow.tolist()),
            float(np.mean(scr[n - _W:n])), float(np.mean(dtr[n - _W:n])),
            float(np.sum(fail[n - _W:n])), _days_since(fail, n),
        ]

    columns = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail", "is_failure_next"]
    training_df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    return training_df, last_x


def has_ml_signal(training_df: pd.DataFrame, min_rows: int = _MIN_ROWS) -> bool:
    if len(training_df) < min_rows:
        return False
    pos_rate = float(training_df["is_failure_next"].mean())
    return 0.05 <= pos_rate <= 0.90
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_automl_templates.py
git commit -m "feat: port predictive-maintenance rolling-window featurization"
```

---

### Task 4: Extract `_build_daily()` in `predictive.py` and write `preprocess()`

**Files:**
- Modify: `backend/app/services/predictive.py`
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py`
- Test: `backend/tests/test_predictive_maintenance_template.py` (new)

**Interfaces:**
- Consumes: `ds_service.validate_columns(dataset, cols)`, `ds_service.dataframe_for(dataset)` (existing, `backend/app/services/datasets.py:206,215`); `pm.build_training_frame`, `pm.has_ml_signal` (Task 3).
- Produces: `predictive._build_daily(df, *, date_col, machine_col, planned_time_col, operating_time_col, total_units_col, good_units_col, target_rate, downtime_category_col) -> tuple[pd.DataFrame grouped, pd.DataFrame daily_ml, float days_per_period]`. `predictive_maintenance.ROLES: list[ColumnRole]`. `predictive_maintenance.preprocess(df: pd.DataFrame, config: dict) -> PreprocessResult` - `config["column_map"]` holds the role->column mapping, `config.get("target_rate")`/`config.get("hourly_value")` are plain values. `context` on the returned `PreprocessResult` carries `{"grouped": DataFrame, "daily_ml": DataFrame, "days_per_period": float, "last_x": dict, "column_map": dict, "hourly_value": float}` for Task 5's `postprocess()` to consume without recomputing.

- [ ] **Step 1: Extract `_build_daily()` in `predictive.py`**

In `backend/app/services/predictive.py`, replace lines 358-414 (the top of `analyze()` through the `daily_ml["is_failure"]` block) - **do not delete `analyze()` yet, that's Step 3 of this task** - first add a new function right before `def analyze(`:

```python
def _build_daily(
    df: pd.DataFrame, *, date_col: str, machine_col: str, planned_time_col: str, operating_time_col: str,
    total_units_col: str, good_units_col: str, target_rate: float | None, downtime_category_col: str | None,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Per-machine daily OEE aggregation, extracted from the old analyze()
    unchanged - shared by the AutoML template's preprocess()/postprocess()
    (app/services/automl_templates/predictive_maintenance.py). Returns
    (grouped, daily_ml, days_per_period): `grouped` keeps the original
    machine_col/date_col names (for postprocess()'s per-machine loop),
    `daily_ml` is renamed to machine/date (for build_training_frame)."""
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    for col in (planned_time_col, operating_time_col, total_units_col, good_units_col):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    grouped = (
        df.groupby([machine_col, pd.Grouper(key=date_col, freq="D")])
        .agg(planned=(planned_time_col, "sum"), operating=(operating_time_col, "sum"),
             total=(total_units_col, "sum"), good=(good_units_col, "sum"))
        .reset_index()
    )
    grouped = grouped[grouped["planned"] > 0]

    if target_rate and target_rate > 0:
        rate = target_rate
    else:
        rates = grouped.apply(lambda r: r["total"] / r["operating"] if r["operating"] > 0 else 0, axis=1)
        rate = float(rates.quantile(0.95)) or 1.0

    def _oee_row(r):
        avail = min(r["operating"] / r["planned"], 1.0) if r["planned"] > 0 else 0.0
        perf = min(r["total"] / (r["operating"] * rate), 1.0) if r["operating"] > 0 else 0.0
        qual = min(r["good"] / r["total"], 1.0) if r["total"] > 0 else 0.0
        return avail, perf, qual, avail * perf * qual

    grouped[["avail", "perf", "qual", "oee"]] = grouped.apply(lambda r: pd.Series(_oee_row(r)), axis=1)

    date_range_days = max((df[date_col].max() - df[date_col].min()).days, 1)
    n_periods = max(grouped[date_col].nunique(), 1)
    days_per_period = date_range_days / n_periods

    daily_ml = grouped.rename(columns={machine_col: "machine", date_col: "date"}).copy()
    daily_ml["scrap_rate"] = (1 - daily_ml["qual"]).clip(0, 1)
    daily_ml["dt_rate"] = (1 - daily_ml["avail"]).clip(0, 1)

    if downtime_category_col and downtime_category_col in df.columns:
        fail_agg = (
            df.assign(_f=df[downtime_category_col].isin(_UNPLANNED_CATS).astype(int))
            .groupby([machine_col, pd.Grouper(key=date_col, freq="D")])["_f"]
            .max().reset_index().rename(columns={machine_col: "machine", date_col: "date", "_f": "is_failure"})
        )
        daily_ml = daily_ml.merge(fail_agg, on=["machine", "date"], how="left")
        daily_ml["is_failure"] = daily_ml["is_failure"].fillna(0).astype(int)
    else:
        daily_ml["is_failure"] = 0

    return grouped, daily_ml, days_per_period
```

- [ ] **Step 2: Simplify `analyze()` to call `_build_daily()`** (still keeping `analyze()` and `_ml_analyze()` for this step - full removal is Task 8)

Replace lines 367-414 of the original `analyze()` body (everything from `df = ds_service.dataframe_for(dataset)` through the `daily_ml["is_failure"] = 0` else-branch) with:

```python
    grouped, daily_ml, days_per_period = _build_daily(
        df, date_col=date_col, machine_col=machine_col, planned_time_col=planned_time_col,
        operating_time_col=operating_time_col, total_units_col=total_units_col, good_units_col=good_units_col,
        target_rate=target_rate, downtime_category_col=downtime_category_col,
    )
```

- [ ] **Step 3: Run the existing predictive test suite to confirm the refactor is behavior-preserving**

Run: `cd backend && python -m pytest tests/test_predictive.py -v`
Expected: 5 passed (this is a pure extraction - no behavior change yet)

- [ ] **Step 4: Write `preprocess()`'s failing test**

Create `backend/tests/test_predictive_maintenance_template.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import predictive_maintenance as pm

# M1: healthy at 0.8 OEE for 30 days. M2: healthy for 10 days, then 20 days
# of failures/low OEE - enough history for the rolling-window featurizer to
# produce >= 40 training rows with a balanced-ish positive rate.
_ROWS = []
for day in range(1, 31):
    _ROWS.append(f"2026-01-{day:02d},M1,480,470,920,900,")
    fail = "1" if day in (11, 18, 25) else ""
    operating = 150 if day >= 11 else 470
    total = 200 if day >= 11 else 920
    good = 150 if day >= 11 else 900
    _ROWS.append(f"2026-01-{day:02d},M2,480,{operating},{total},{good},{fail}")
PREDICTIVE_CSV_ROWS = "\n".join(_ROWS)


def _fixture_df() -> pd.DataFrame:
    from io import StringIO
    return pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + PREDICTIVE_CSV_ROWS + "\n"))


def test_preprocess_returns_engine_ready_result_with_enough_history():
    df = _fixture_df()
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure",
        },
    }
    result = pm.preprocess(df, config)
    assert result.skip_ml is False
    assert result.task == "classification"
    assert result.target_col == "is_failure_next"
    assert set(result.feature_cols) == {"oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"}
    assert len(result.df) > 0
    assert "grouped" in result.context and "last_x" in result.context


def test_preprocess_flags_insufficient_signal_for_short_history():
    # Only 8 days total - nowhere near build_training_frame's W+H window
    # requirements, so preprocess() must signal skip_ml rather than handing
    # an empty/tiny frame to automl_engine.
    rows = [f"2026-01-{day:02d},M1,480,470,920,900," for day in range(1, 9)]
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
        },
    }
    result = pm.preprocess(df, config)
    assert result.skip_ml is True
    assert any("not enough" in n.lower() or "insufficient" in n.lower() for n in result.notes)
    assert "grouped" in result.context  # postprocess() still needs this for the statistical fallback


def test_preprocess_rejects_missing_columns():
    df = _fixture_df()
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "does_not_exist",
    }}
    with pytest.raises(ValueError):
        pm.preprocess(df, config)
```

- [ ] **Step 5: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: FAIL with `AttributeError: module 'app.services.automl_templates.predictive_maintenance' has no attribute 'preprocess'`

- [ ] **Step 6: Write `preprocess()` and `ROLES`**

Append to `backend/app/services/automl_templates/predictive_maintenance.py`:

```python
from app.services.automl_templates import ColumnRole, PreprocessResult

ROLES = [
    ColumnRole("date_col", "Date column"),
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("planned_time_col", "Planned time"),
    ColumnRole("operating_time_col", "Operating time"),
    ColumnRole("total_units_col", "Total units"),
    ColumnRole("good_units_col", "Good units"),
    ColumnRole("failure_col", "Failure flag column", required=False),
    ColumnRole("downtime_category_col", "Downtime category", required=False),
]

# Excludes SVC/LinearSVC - automl_engine's SVC has no probability=True, so it
# has no .predict_proba(), which postprocess() needs for a risk score, not
# just a class label.
_SAFE_ALGORITHMS = [
    "LogisticRegression", "RandomForestClassifier", "ExtraTreesClassifier",
    "HistGradientBoostingClassifier", "GradientBoostingClassifier", "KNeighborsClassifier",
    "XGBoostClassifier", "LightGBMClassifier",
]


def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
    cols = config.get("column_map", {})
    required = ["date_col", "machine_col", "planned_time_col", "operating_time_col", "total_units_col", "good_units_col"]
    missing_roles = [r for r in required if not cols.get(r)]
    if missing_roles:
        raise ValueError(f"Missing required column mapping for: {', '.join(missing_roles)}")
    for role_key, col_name in cols.items():
        if col_name and col_name not in df.columns:
            raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

    grouped, daily_ml, days_per_period = legacy._build_daily(
        df, date_col=cols["date_col"], machine_col=cols["machine_col"],
        planned_time_col=cols["planned_time_col"], operating_time_col=cols["operating_time_col"],
        total_units_col=cols["total_units_col"], good_units_col=cols["good_units_col"],
        target_rate=config.get("target_rate"), downtime_category_col=cols.get("downtime_category_col"),
    )

    training_df, last_x = build_training_frame(daily_ml)
    context = {
        "grouped": grouped, "daily_ml": daily_ml, "days_per_period": days_per_period,
        "last_x": last_x, "column_map": cols, "hourly_value": config.get("hourly_value", 1000.0),
        "n_training_rows": len(training_df),
        "failure_col": cols.get("failure_col"), "downtime_category_col": cols.get("downtime_category_col"),
    }

    if not has_ml_signal(training_df):
        return PreprocessResult(
            skip_ml=True,
            notes=[f"Not enough rolling-window history to train a classifier ({len(training_df)} usable "
                   f"windows, need >= {_MIN_ROWS} with a 5-90% failure rate) - using the statistical risk model instead."],
            context=context,
        )

    return PreprocessResult(
        skip_ml=False,
        df=training_df,
        target_col="is_failure_next",
        task="classification",
        feature_cols=["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"],
        algorithms=_SAFE_ALGORITHMS,
        notes=[f"Trained on {len(training_df)} rolling 7-day windows across all machines."],
        context=context,
    )
```

Add the missing import at the top of the file: `from app.services.automl_templates import ColumnRole, PreprocessResult` (already shown above - place it with the other imports, not mid-file).

- [ ] **Step 7: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: 3 passed

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/predictive.py backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: extract _build_daily and add predictive-maintenance preprocess()"
```

---

### Task 5: Write `postprocess()`

**Files:**
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py`
- Test: `backend/tests/test_predictive_maintenance_template.py` (append)

**Interfaces:**
- Consumes: `legacy._rul_days`, `legacy._top_driver`, `legacy._stat_analyze`, `legacy._ACTIONS`, `legacy._PM_HOURS`, `legacy._recent_slope`, `legacy._linreg_slope`, `legacy._ML_FEAT_LABELS`, `legacy.MachineRisk/ModelMeta/MaintenanceRec/CostSummary/PredictiveResult` dataclasses (all existing, unchanged, `backend/app/services/predictive.py`).
- Produces: `postprocess(engine_result: dict | None, config: dict, context: dict) -> dict` returning `{"predictive_result": {...}, "pipeline": Pipeline | None, "explain_X": DataFrame | None, "explain_y": Series | None, "leaderboard": list, "best_model_name": str | None, "metric_name": str | None, "metric_value": float | None}`. `predictive_result` matches `PredictiveResponse`'s existing shape (`backend/app/api/routes/predictive.py:71-75`) exactly, so `Predictive.tsx` needs zero rendering changes.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_predictive_maintenance_template.py`:

```python
from app.services import automl_engine


def test_postprocess_with_trained_model_produces_predictive_result():
    df = _fixture_df()
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure", "downtime_category_col": "downtime_category",
        },
        "hourly_value": 1000.0,
    }
    pre = pm.preprocess(df, config)
    assert pre.skip_ml is False

    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, algorithms=pre.algorithms, thoroughness="fast",
    )

    out = pm.postprocess(engine_result, config, pre.context)
    result = out["predictive_result"]
    machines = {m["machine"]: m for m in result["machines"]}
    assert "M1" in machines and "M2" in machines
    assert all(0.0 <= m["failure_probability"] <= 1.0 for m in machines.values())
    assert all(m["ml_powered"] is True for m in machines.values())
    assert result["model_meta"] is not None
    assert result["model_meta"]["algorithm"] in pm._SAFE_ALGORITHMS
    assert out["pipeline"] is not None
    assert out["explain_X"] is not None
    assert result["cost_summary"]["machines_at_risk"] >= 0


def test_postprocess_without_trained_model_falls_back_to_statistical():
    rows = [f"2026-01-{day:02d},M1,480,{150 if day > 5 else 470},{200 if day > 5 else 920},{150 if day > 5 else 900}," for day in range(1, 9)]
    from io import StringIO
    df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
    }, "hourly_value": 1000.0}

    pre = pm.preprocess(df, config)
    assert pre.skip_ml is True

    out = pm.postprocess(None, config, pre.context)
    result = out["predictive_result"]
    assert len(result["machines"]) == 1
    assert result["machines"][0]["ml_powered"] is False
    assert result["model_meta"] is None
    assert out["pipeline"] is None
    assert out["explain_X"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k postprocess`
Expected: FAIL with `AttributeError: ... has no attribute 'postprocess'`

- [ ] **Step 3: Write `postprocess()`**

Append to `backend/app/services/automl_templates/predictive_maintenance.py`:

```python
def _machine_risk_from_prob(
    machine: str, fail_prob: float, ml_powered: bool, drivers: list[dict],
    oee_s: list[float], avail_s: list[float], perf_s: list[float], qual_s: list[float],
    last_fail_ago: float | None, days_per_period: float,
) -> tuple[dict, str]:
    current_oee = oee_s[-1]
    slope = legacy._recent_slope(oee_s)
    avg_a, avg_p, avg_q = sum(avail_s) / len(avail_s), sum(perf_s) / len(perf_s), sum(qual_s) / len(qual_s)
    driver = legacy._top_driver(avg_a, avg_p, avg_q)

    accel = 0.0
    if len(oee_s) >= 14:
        accel = legacy._linreg_slope(oee_s[-7:]) - legacy._linreg_slope(oee_s[-14:-7])
    rul = legacy._rul_days(current_oee, slope, days_per_period, accel=accel)

    if fail_prob >= 0.65 or current_oee < legacy._CRITICAL_OEE:
        risk = "high"
    elif fail_prob >= 0.35 or current_oee < legacy._WARN_OEE:
        risk = "medium"
    else:
        risk = "low"

    return {
        "machine": machine, "current_oee": round(current_oee, 4), "trend_slope": round(slope, 5),
        "failure_probability": round(fail_prob, 3), "risk_level": risk, "top_driver": driver, "rul_days": rul,
        "last_failure_days_ago": last_fail_ago, "ml_powered": ml_powered, "ml_drivers": drivers,
    }, risk


def postprocess(engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    grouped = context["grouped"]
    days_per_period = context["days_per_period"]
    hourly_value = float(context.get("hourly_value") or 1000.0)
    cols = context["column_map"]
    machine_col, date_col = cols["machine_col"], cols["date_col"]
    failure_col, downtime_category_col = context.get("failure_col"), context.get("downtime_category_col")

    pipeline = engine_result["pipeline"] if engine_result else None
    model_meta_dict: dict[str, Any] | None = None
    if engine_result:
        best_scores = engine_result["meta"]["scores"]
        model_meta_dict = {
            "algorithm": engine_result["meta"]["model_name"],
            "accuracy_pct": round(float(best_scores.get("accuracy", 0.0)) * 100, 1),
            "n_training_samples": context.get("n_training_rows", 0),
            "top_features": [],  # filled in by Plan 2's explainability layer
        }

    machines_out: list[dict] = []
    recs_out: list[dict] = []

    for machine, mdf in grouped.groupby(machine_col):
        mdf = mdf.sort_values(date_col)
        oee_s, avail_s, perf_s, qual_s = mdf["oee"].tolist(), mdf["avail"].tolist(), mdf["perf"].tolist(), mdf["qual"].tolist()
        if not oee_s:
            continue
        machine_key = str(machine)

        last_fail_ago: float | None = None
        machine_max_date = mdf[date_col].max()
        # `grouped` doesn't retain the raw failure_col/downtime_category_col rows
        # (they're aggregated away) - last-failure lookback re-derives from
        # daily_ml's own is_failure column instead of the original df, since
        # that's what's already available in context and matches what
        # build_training_frame trained on.
        mfail = context["daily_ml"][(context["daily_ml"]["machine"] == machine_key) & (context["daily_ml"]["is_failure"] > 0)]
        if not mfail.empty:
            last_fail_ago = float((machine_max_date - mfail["date"].max()).days)

        if pipeline is not None and machine_key in context["last_x"]:
            x_row = context["last_x"][machine_key]
            feature_cols = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"]
            x_df = pd.DataFrame([dict(zip(feature_cols, x_row))])
            fail_prob = float(pipeline.predict_proba(x_df)[0][1])
            ml_powered = True
            drivers: list[dict] = []
        else:
            fail_prob, driver_contribs = legacy._stat_analyze(
                oee_s, avail_s, perf_s, qual_s, last_fail_ago,
                fleet_oee_median=sorted(oee_s)[len(oee_s) // 2] if oee_s else 0.0,
            )
            ml_powered = False
            drivers = driver_contribs

        machine_dict, risk = _machine_risk_from_prob(
            machine_key, fail_prob, ml_powered, drivers, oee_s, avail_s, perf_s, qual_s, last_fail_ago, days_per_period,
        )
        machines_out.append(machine_dict)

        priority = {"high": "immediate", "medium": "scheduled", "low": "monitor"}[risk]
        action, reason = legacy._ACTIONS.get(machine_dict["top_driver"], ("General inspection recommended", "Multiple metrics declining"))
        pm_hours = legacy._PM_HOURS[priority]
        avoided_h = round(fail_prob * 48.0, 1)
        net_saving = round((avoided_h - pm_hours) * hourly_value, 2)
        recs_out.append({
            "machine": machine_key, "priority": priority, "action": action, "reason": reason,
            "estimated_pm_hours": pm_hours, "avoided_downtime_hours": avoided_h, "net_saving": net_saving,
        })

    risk_order = {"high": 0, "medium": 1, "low": 2}
    machines_out.sort(key=lambda m: (risk_order.get(m["risk_level"], 3), -m["failure_probability"]))
    recs_out.sort(key=lambda r: {"immediate": 0, "scheduled": 1, "monitor": 2}.get(r["priority"], 3))

    at_risk = sum(1 for m in machines_out if m["risk_level"] in ("high", "medium"))
    total_avoided = sum(r["avoided_downtime_hours"] * hourly_value for r in recs_out)
    total_pm = sum(r["estimated_pm_hours"] * hourly_value for r in recs_out)
    net_savings = round(total_avoided - total_pm, 2)
    roi_pct = round((net_savings / total_pm * 100) if total_pm > 0 else 0.0, 1)

    predictive_result = {
        "machines": machines_out,
        "recommendations": recs_out,
        "cost_summary": {
            "total_avoided_cost": round(total_avoided, 2), "total_pm_cost": round(total_pm, 2),
            "net_savings": net_savings, "roi_pct": roi_pct, "machines_at_risk": at_risk,
        },
        "model_meta": model_meta_dict,
    }

    return {
        "predictive_result": predictive_result,
        "pipeline": pipeline,
        "explain_X": engine_result["explain_X"] if engine_result else None,
        "explain_y": engine_result["explain_y"] if engine_result else None,
        "leaderboard": engine_result["leaderboard"] if engine_result else [],
        "best_model_name": engine_result["meta"]["model_name"] if engine_result else None,
        "metric_name": engine_result["meta"]["metric_name"] if engine_result else None,
        "metric_value": engine_result["meta"]["metric_value"] if engine_result else None,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: add predictive-maintenance postprocess() with statistical fallback"
```

---

### Task 6: Wire templates into `automl_runner.py`

**Files:**
- Modify: `backend/app/services/automl_runner.py`
- Test: `backend/tests/test_automl_templates.py` (append) - runner-level test using the `_test_template` pattern, not a full API test (that's Task 7).

**Interfaces:**
- Consumes: `automl_templates.get_template(template_id)` (Task 2), `AutoMLTemplate.preprocess`/`postprocess` (Task 2/4/5).
- Produces: `_execute()` now persists `run.leaderboard`, `run.best_model_name`, `run.metric_name`, `run.metric_value`, `run.meta` (with `meta["predictive_result"]` or equivalent template payload merged in), `run.model_path` (`None` when the template's postprocess returned `pipeline=None`) for template runs, exactly the same fields the generic path already sets.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_automl_templates.py`:

```python
import uuid

import pytest_asyncio
from sqlalchemy import select

from app.db import session as db_session
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.models.user import User
from app.services import automl_templates as templates
from app.services.automl_runner import execute_automl_run


@pytest.mark.asyncio
async def test_execute_automl_run_routes_through_template(client):
    # Register a trivial template that always skips ML, to test the runner's
    # branch/persistence logic in isolation from any real training.
    t = templates.AutoMLTemplate(
        id="_test_runner_template",
        label="Test Runner Template",
        roles=[],
        preprocess=lambda df, config: templates.PreprocessResult(skip_ml=True, notes=["always skips"], context={"seen_rows": len(df)}),
        postprocess=lambda engine_result, config, context: {
            "predictive_result": {"marker": "ok", "rows": context["seen_rows"]},
            "pipeline": None, "explain_X": None, "explain_y": None,
            "leaderboard": [], "best_model_name": None, "metric_name": None, "metric_value": None,
        },
    )
    templates.register_template(t)

    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": "correct-horse-battery-staple1"})  # pragma: allowlist secret
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    up = await client.post("/datasets", headers=headers, files={"file": ("t.csv", b"a,b\n1,2\n3,4\n", "text/csv")})
    dataset_id = up.json()["id"]

    async with db_session.async_session_factory() as db:
        ds_row = (await db.execute(select(Dataset).where(Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
        owner = (await db.execute(select(User).where(User.org_id == ds_row.org_id))).scalars().first()
        run = AutoMLRun(
            org_id=ds_row.org_id, dataset_id=ds_row.id, created_by=owner.id,
            name="test", task="classification", target_column="b", template_id="_test_runner_template", status="queued",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id, org_id_val = str(run.id), str(run.org_id)

    await execute_automl_run(run_id, org_id_val)

    async with db_session.async_session_factory() as db:
        refreshed = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == uuid.UUID(run_id)))).scalar_one()
        assert refreshed.status == "success"
        assert refreshed.meta["predictive_result"] == {"marker": "ok", "rows": 2}
        assert refreshed.model_path is None

    del templates._REGISTRY["_test_runner_template"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v -k template_routes`
Expected: FAIL - `refreshed.meta` will be the generic-path shape (or the run will fail, since `target_column="b"` combined with no real training makes the generic path attempt normal training and either succeed differently or the template branch doesn't exist yet, so `run.template_id` is set but ignored) - either way, `assert refreshed.meta["predictive_result"] == ...` fails with `KeyError` or a mismatch.

- [ ] **Step 3: Modify `automl_runner.py`**

Read the current file first (`backend/app/services/automl_runner.py`) - modify the `try:` block inside `_execute()`:

```python
    try:
        df = await asyncio.to_thread(dataframe_for, dataset)
        config = run.config or {}

        if run.template_id:
            template = automl_templates.get_template(run.template_id)
            pre = await asyncio.to_thread(template.preprocess, df, config)

            if pre.skip_ml:
                template_out = await asyncio.to_thread(template.postprocess, None, config, pre.context)
            else:
                engine_result = await asyncio.to_thread(
                    automl_engine.train_automl_baseline,
                    pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
                    feature_columns=pre.feature_cols, algorithms=pre.algorithms,
                    thoroughness=config.get("thoroughness", "balanced"),
                )
                template_out = await asyncio.to_thread(template.postprocess, engine_result, config, pre.context)

            if pre.task:
                run.task = pre.task
            if pre.target_col:
                run.target_column = pre.target_col
            run.status = "success"
            run.leaderboard = template_out["leaderboard"]
            run.best_model_name = template_out["best_model_name"]
            run.metric_name = template_out["metric_name"]
            run.metric_value = template_out["metric_value"]
            meta = {k: v for k, v in template_out.items() if k not in ("pipeline", "explain_X", "explain_y", "leaderboard", "best_model_name", "metric_name", "metric_value")}
            meta["notes"] = pre.notes

            if template_out["pipeline"] is not None:
                run_dir = automl_storage.run_dir(org_id, run_id)
                model_path = run_dir / "model.joblib"
                joblib.dump(template_out["pipeline"], model_path)
                template_out["explain_X"].to_csv(run_dir / "explain_X.csv", index=False)
                pd.DataFrame({"y": template_out["explain_y"]}).to_csv(run_dir / "explain_y.csv", index=False)
                run.model_path = str(model_path)
            else:
                run.model_path = None

            run.meta = meta
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return

        result = await asyncio.to_thread(
            automl_engine.train_automl_baseline,
            df,
            run.target_column,
            automl_engine.AutoMLTask(run.task),
            feature_columns=run.feature_columns or None,
            time_column=config.get("time_column"),
            horizon=int(config.get("horizon", 1)),
            algorithms=config.get("algorithms") or None,
            thoroughness=config.get("thoroughness", "balanced"),
            handle_class_imbalance=bool(config.get("handle_class_imbalance", True)),
            forecast_strategy=config.get("forecast_strategy", "direct"),
            time_budget_seconds=config.get("time_budget_seconds"),
            search_method=config.get("search_method", "random"),
        )
    except Exception as exc:  # noqa: BLE001 - training failure is reported on the run, not raised
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return
```

The rest of the function (the existing `run_dir = ...` through the final `await db.commit()` for the **generic** path) stays exactly as-is below this - it's only reached now when `run.template_id` is falsy, since the template branch `return`s early after its own commit.

Add the new import at the top of `automl_runner.py`: `from app.services import automl_templates`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_templates.py -v`
Expected: 6 passed (5 from Task 3 + this one)

- [ ] **Step 5: Run the full existing AutoML suite to confirm the generic path is untouched**

Run: `cd backend && python -m pytest tests/test_automl.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/automl_runner.py backend/tests/test_automl_templates.py
git commit -m "feat: route AutoMLRun.template_id through preprocess/postprocess in automl_runner"
```

---

### Task 7: API routes for templates

**Files:**
- Modify: `backend/app/api/routes/automl.py`
- Test: `backend/tests/test_predictive_maintenance_template.py` (append) - full end-to-end API test.

**Interfaces:**
- Produces: `GET /automl/templates -> list[{"id": str, "label": str, "roles": [{"key": str, "label": str, "required": bool}]}]`. `POST /automl/templates/{template_id}/runs` body `{"name": str, "dataset_id": UUID, "column_map": dict[str, str], "config": dict = {}}` -> `RunDetail` (same shape as `POST /automl/runs`), 202. 404 if `template_id` is unknown.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_predictive_maintenance_template.py`:

```python
import pytest

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_templates_includes_predictive_maintenance(client):
    token = await _register_and_login(client)
    res = await client.get("/automl/templates", headers=_h(token))
    assert res.status_code == 200
    ids = {t["id"] for t in res.json()}
    assert "predictive_maintenance" in ids


@pytest.mark.asyncio
async def test_create_predictive_maintenance_run_end_to_end(client):
    token = await _register_and_login(client)
    csv_rows = []
    for day in range(1, 31):
        csv_rows.append(f"2026-01-{day:02d},M1,480,470,920,900,,")
        is_fail_day = day in (11, 18, 25)
        fail = "1" if is_fail_day else ""
        category = "Mechanical Failure" if is_fail_day else ""
        operating = 150 if day >= 11 else 470
        total = 200 if day >= 11 else 920
        good = 150 if day >= 11 else 900
        csv_rows.append(f"2026-01-{day:02d},M2,480,{operating},{total},{good},{fail},{category}")
    csv_bytes = ("date,machine,planned,operating,total,good,failure,downtime_category\n" + "\n".join(csv_rows) + "\n").encode()

    up = await client.post("/datasets", headers=_h(token), files={"file": ("pm.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "PM run", "dataset_id": dataset_id,
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure", "downtime_category_col": "downtime_category",
        },
        "config": {"hourly_value": 1000.0},
    }
    created = await client.post("/automl/templates/predictive_maintenance/runs", headers=_h(token), json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=_h(token))
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["meta"]["predictive_result"]["machines"]
    machines = {m["machine"]: m for m in run["meta"]["predictive_result"]["machines"]}
    assert "M1" in machines and "M2" in machines


@pytest.mark.asyncio
async def test_create_run_for_unknown_template_404s(client):
    token = await _register_and_login(client)
    up = await client.post("/datasets", headers=_h(token), files={"file": ("t.csv", b"a,b\n1,2\n", "text/csv")})
    dataset_id = up.json()["id"]
    res = await client.post(
        "/automl/templates/does_not_exist/runs", headers=_h(token),
        json={"name": "x", "dataset_id": dataset_id, "column_map": {}},
    )
    assert res.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k "template or unknown_template"`
Expected: FAIL with 404 (route doesn't exist yet - FastAPI returns 404 for unmatched routes too, so check the error is actually "not found" for the right reason by running with `-v` and reading the failure - the `test_list_templates_includes_predictive_maintenance` test's `assert res.status_code == 200` will fail since the route doesn't exist)

- [ ] **Step 3: Add the routes**

In `backend/app/api/routes/automl.py`, add near the top (after existing imports):

```python
from app.services import automl_templates
```

Add two new Pydantic models near `TrainRequest`:

```python
class TemplateRoleResponse(BaseModel):
    key: str
    label: str
    required: bool


class TemplateSummary(BaseModel):
    id: str
    label: str
    roles: list[TemplateRoleResponse]


class TemplateRunRequest(BaseModel):
    name: str
    dataset_id: uuid.UUID
    column_map: dict[str, str]
    config: dict[str, Any] = {}
```

Add two new routes (place after `get_algorithms`, before `list_runs`):

```python
@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates(current: CurrentUser = Depends(get_current_user)):
    return [
        TemplateSummary(
            id=t.id, label=t.label,
            roles=[TemplateRoleResponse(key=r.key, label=r.label, required=r.required) for r in t.roles],
        )
        for t in automl_templates.list_templates()
    ]


@router.post("/templates/{template_id}/runs", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_template_run(
    template_id: str,
    req: TemplateRunRequest,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        template = automl_templates.get_template(template_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown AutoML template '{template_id}'") from None

    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, req.dataset_id)

    run = AutoMLRun(
        org_id=current.org_id,
        dataset_id=dataset.id,
        created_by=current.id,
        name=req.name,
        task="classification",  # placeholder until the run finishes - automl_runner._execute()'s
                                 # template branch (Task 6) overwrites this with pre.task once
                                 # preprocess() runs; queued/running polls will read this default.
        target_column="",       # same - overwritten with pre.target_col once preprocess() runs
        template_id=template.id,
        config={"column_map": req.column_map, **req.config},
        status="queued",
    )
    db.add(run)
    await log_event(db, action="automl.template_train", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name, "template_id": template.id})
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_automl_run, str(run.id), str(current.org_id))
    return _detail(run)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: all passed (the module now has: 3 preprocess tests + 2 postprocess tests + 3 route tests = 8 passed - the runner test from Task 6 lives in `test_automl_templates.py`, not this file)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/automl.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: add GET /automl/templates and POST /automl/templates/{id}/runs"
```

---

### Task 8: Register the template, remove the old synchronous route

**Files:**
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py`
- Modify: `backend/app/services/predictive.py`
- Modify: `backend/app/api/routes/predictive.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_predictive.py`

**Interfaces:**
- Produces: `automl_templates.list_templates()` includes an entry with `id="predictive_maintenance"` after import (side-effecting registration, matching the `# noqa: F401 - register models` pattern already used in `tests/conftest.py`).

- [ ] **Step 1: Register the template**

Append to the bottom of `backend/app/services/automl_templates/predictive_maintenance.py`:

```python
register_template(AutoMLTemplate(
    id="predictive_maintenance",
    label="Predictive Maintenance",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=True,
    min_positive_examples=_MIN_ROWS,
))
```

Add `register_template, AutoMLTemplate` to the existing `from app.services.automl_templates import ColumnRole, PreprocessResult` import line, making it `from app.services.automl_templates import AutoMLTemplate, ColumnRole, PreprocessResult, register_template`.

Ensure this module is imported at app startup so registration actually runs. In `backend/app/services/automl_templates/__init__.py`, add at the very bottom of the file (after the registry functions):

```python
from app.services.automl_templates import predictive_maintenance  # noqa: F401 - registers the template
```

- [ ] **Step 2: Run the template test suite to confirm registration works via the package import**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py::test_list_templates_includes_predictive_maintenance -v`
Expected: PASS (this already passed in Task 7's Step 4 by direct submodule import - this step confirms it also works via the package-level import path the rest of the app uses)

- [ ] **Step 3: Delete `_ml_analyze()` and `analyze()` from `predictive.py`**

In `backend/app/services/predictive.py`:
- Delete the entire `_ml_analyze()` function (the block starting `def _ml_analyze(daily: pd.DataFrame) -> tuple[dict | None, dict | None, dict | None]:` through its final `return machine_probs, machine_drivers, meta`).
- Delete the entire `analyze()` function (starting `def analyze(` through its final `return PredictiveResult(...)`).
- Delete the now-unused ML imports at the top: the `_HAS_ML` try/except block importing `GradientBoostingClassifier`/`GroupKFold`/`TimeSeriesSplit`/`cross_val_score`.
- Keep everything else: the dataclasses, `_linreg_slope`, `_std`, `_recent_slope`, `_build_daily`, `_stat_analyze`, `_rul_days`, `_top_driver`, `_ACTIONS`, `_PM_HOURS`, `_CRITICAL_OEE`, `_WARN_OEE`, `_UNPLANNED_CATS`, `_ML_FEATURES`, `_ML_FEAT_LABELS`, `_TREND_WINDOW_DAYS`, `_RUL_MAX_DAYS`, and the entire schedule CRUD section at the bottom.

- [ ] **Step 4: Remove the synchronous route**

In `backend/app/api/routes/predictive.py`:
- Delete `analyze_router = APIRouter(...)` and everything under `# ─── (implicit) ───` down through the `analyze_predictive` function (lines 16, 20-92 of the original file - i.e. `PredictiveRequest`, `MachineRiskResponse`, `ModelMetaResponse`, `MaintenanceRecResponse`, `CostSummaryResponse`, `PredictiveResponse`, and `analyze_predictive`).
- Keep `router = APIRouter(prefix="/predictive", tags=["predictive"])` and everything from `# ─── Maintenance schedule CRUD ─────` onward, unchanged.

- [ ] **Step 5: Unregister the removed router**

In `backend/app/main.py`, delete the line `app.include_router(predictive.analyze_router)`. Keep `app.include_router(predictive.router)`.

- [ ] **Step 6: Update `test_predictive.py`**

In `backend/tests/test_predictive.py`, delete `test_analyze_flags_low_oee_machine_high_risk` and `test_analyze_rejects_missing_columns` (they hit the now-removed route). Keep `PREDICTIVE_CSV`, `_register_and_login`, `_h`, `_upload`, and the 3 schedule tests unchanged - `_upload`/`PREDICTIVE_CSV` become unused by this file once the two tests are gone, so delete them too if nothing else in the file references them (check with `grep -n PREDICTIVE_CSV tests/test_predictive.py` after deleting - if only the import/definition lines remain, remove those as well).

- [ ] **Step 7: Run the full predictive + template + automl suites**

Run: `cd backend && python -m pytest tests/test_predictive.py tests/test_automl.py tests/test_automl_templates.py tests/test_predictive_maintenance_template.py -v`
Expected: all passed (test_predictive.py: 3 passed, test_automl.py: 7 passed, test_automl_templates.py: 6 passed, test_predictive_maintenance_template.py: 8 passed)

- [ ] **Step 8: Run the full backend suite to catch anything else referencing the removed functions**

Run: `cd backend && python -m pytest -q`
Expected: all passed, 0 failed. If anything fails with `ImportError`/`AttributeError` referencing `predictive.analyze`, `predictive._ml_analyze`, or `predictive.analyze_router`, find and update that caller (grep first: `grep -rn "predictive.analyze\|predictive\.analyze_router\|_ml_analyze" backend/app backend/tests`).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/app/services/automl_templates/__init__.py backend/app/services/predictive.py backend/app/api/routes/predictive.py backend/app/main.py backend/tests/test_predictive.py
git commit -m "refactor: retire predictive.py's hardcoded model in favor of the AutoML template"
```

---

### Task 9: Update the frontend `Predictive.tsx` page

**Files:**
- Modify: `frontend/src/pages/Predictive.tsx`

**Interfaces:**
- Consumes: `POST /automl/templates/predictive_maintenance/runs` (Task 7), `GET /automl/runs/{id}` (existing, `backend/app/api/routes/automl.py:184`).
- No change to `MachineRisk`/`MaintenanceRec`/`PredictiveResult` interfaces or any rendering JSX in this file - `postprocess()` (Task 5) was written to produce this exact shape under `run.meta.predictive_result`.

- [ ] **Step 1: Replace the `analyze` mutation**

In `frontend/src/pages/Predictive.tsx`, replace the `analyze` mutation (lines 154-161) with:

```tsx
  const analyze = useMutation({
    mutationFn: async () => {
      const body = {
        name: `Predictive maintenance ${new Date().toISOString()}`,
        dataset_id: datasetId,
        column_map: cols,
        config: { hourly_value: Number(hourlyValue) || 1000 },
      };
      const created = await apiFetch("/automl/templates/predictive_maintenance/runs", { method: "POST", body: JSON.stringify(body) });
      if (!created.ok) throw new Error((await created.json()).detail ?? "Failed to start analysis");
      const { id: runId } = await created.json();

      for (let attempt = 0; attempt < 60; attempt++) {
        const poll = await apiFetch(`/automl/runs/${runId}`);
        if (!poll.ok) throw new Error("Failed to check run status");
        const run = await poll.json();
        if (run.status === "success") return run.meta.predictive_result as PredictiveResult;
        if (run.status === "failed") throw new Error(run.error ?? "Analysis failed");
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      throw new Error("Analysis is taking longer than expected - check back on the Runs page shortly");
    },
  });
```

No other lines in the file change - `analyze.data`, `analyze.isPending`, `analyze.isError`, `analyze.error` are all still the same react-query mutation fields the JSX below already reads.

- [ ] **Step 2: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors referencing `Predictive.tsx`

- [ ] **Step 3: Manual verification**

Start the dev server and confirm the page still renders and the Analyze button still works end-to-end against a running backend (this task has no automated frontend test harness in this repo - `grep -rn "test" frontend/src/pages/Predictive.tsx` confirms there isn't an existing one to extend, and adding one is out of scope for this plan).

Run: `cd frontend && npm run dev` (or however this repo's dev server is started - check `frontend/package.json`'s `scripts` if `npm run dev` isn't it), navigate to the Predictive Maintenance page, pick a dataset with enough history, map columns, click Analyze, confirm the machine risk table and recommendations render after a short delay.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Predictive.tsx
git commit -m "feat: switch Predictive Maintenance page to async AutoML template run"
```

---

### Task 10: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all passed, 0 failed, 0 errors.

- [ ] **Step 2: Run the frontend type check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint` (check `frontend/package.json` for the exact lint script name if `npm run lint` doesn't exist)
Expected: no errors.

- [ ] **Step 3: Confirm no leftover references to removed code**

Run: `grep -rn "_ml_analyze\|analyze_router\|predictive\.analyze\b" backend/app backend/tests frontend/src`
Expected: no matches (aside from this plan document itself, which isn't part of the grep target).

- [ ] **Step 4: Update the design spec's status**

The spec at `docs/superpowers/specs/2026-07-17-manufacturing-automl-design.md` describes Phases 0-3.5 and alerting; this plan only implements the template registry + Predictive Maintenance (the foundation). No spec edit is needed - the spec doesn't track per-plan completion, and this plan's own header already scopes what it covers.

- [ ] **Step 5: Report status to the user**

Summarize: template registry shipped, Predictive Maintenance re-platformed onto the shared bake-off engine with its RUL/cost/statistical-fallback math fully preserved, old synchronous endpoint removed and replaced with an async run-then-poll flow. Remaining plans (rare-event routing + explainability, time-series cleaning, Scrap/Downtime templates, real-time scoring, alerting) are still to be written and executed separately, per the breakdown agreed at the start of planning.

---

## Self-Review Notes

**Spec coverage:** This plan implements the "Architecture" and "The three templates" sections' Predictive Maintenance portion of the spec, plus the "Data model" section's `template_id` column. It deliberately does **not** implement: Scrap/Downtime templates, explainability/recommendations (`automl_explain_nl.py`), rare-event routing (`automl_rare_event.py`), time-series cleaning (`timeseries_quality.py`), real-time scoring/drift (`automl_realtime.py`, `AutoMLLiveBinding`), or alerting (`_eval_automl_risk`) - all six are separate follow-on plans per the dependency ordering agreed before this plan was written.

**Placeholder scan:** No TBD/TODO markers. `AutoMLRun`'s `task="classification"`/`target_column=""` placeholders set at creation in Task 7 Step 3 are genuinely overwritten by `automl_runner._execute()`'s template branch (Task 6, `run.task = pre.task` / `run.target_column = pre.target_col`) once `preprocess()` runs - not a stub left unresolved.

**Type consistency:** `PreprocessResult`, `AutoMLTemplate`, `ColumnRole` (Task 2) are used with identical field names across Tasks 3-8. `postprocess()`'s return dict shape (Task 5) matches exactly what `automl_runner.py` destructures in Task 6. `predictive_result`'s nested shape (Task 5) matches `Predictive.tsx`'s existing `PredictiveResult` TypeScript interface (Task 9) field-for-field: `machines[].{machine, current_oee, failure_probability, risk_level, top_driver, rul_days, ml_powered}`, `recommendations[].{machine, priority, action, reason, estimated_pm_hours, avoided_downtime_hours, net_saving}`, `cost_summary.{total_avoided_cost, total_pm_cost, net_savings, roi_pct, machines_at_risk}`, `model_meta.{algorithm, accuracy_pct, n_training_samples}` (top_features intentionally empty until Plan 2).
