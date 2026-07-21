# Scrap Prediction + Downtime Risk AutoML Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two more guided AutoML templates - Scrap Prediction and Downtime Risk - reusing the template registry, runner wiring, and generic API routes already built and shipped in the Predictive Maintenance plan, with zero changes to `automl_engine.py`, `automl_runner.py`, or `automl.py`'s routes.

**Architecture:** Unlike Predictive Maintenance (which had real legacy RUL/cost/statistical-fallback math to preserve), neither Scrap Prediction nor Downtime Risk has any existing ML behavior to port - `scrap.py` is pure SQL aggregation, and downtime data has no ML today at all. Both templates are simple row-level regression: map columns, predict a numeric target from the rest, summarize the winning model. That similarity is real, not coincidental, so both are built on one shared helper (`automl_templates/_shared.py`) rather than duplicating the same preprocess/postprocess logic twice.

**Tech Stack:** FastAPI, pandas, the existing `automl_engine.train_automl_baseline()` (unmodified), the existing `automl_templates` registry and `automl_runner.py` template branch (both from the Predictive Maintenance plan, unmodified).

## Global Constraints

- `automl_engine.py`, `automl_runner.py`'s template branch, and `automl.py`'s `GET /automl/templates` / `POST /automl/templates/{template_id}/runs` routes are NOT modified by this plan - both templates plug into infrastructure that already works for any registered template. If you find yourself needing to change any of those three, stop and reconsider the template's design instead.
- Every `AutoMLTemplate.preprocess()` must raise `ValueError` for missing/invalid column mappings or too little data (not silently proceed with garbage), matching the existing `predictive_maintenance.py` convention.
- Test password policy (12+ chars, letter + digit): reuse `"correct-horse-battery-staple1"` with a trailing ` # pragma: allowlist secret` comment on any line where it's a standalone string literal (pre-commit secret scanner).
- Column-role mapping validation pattern: every mapped column must exist in the dataset's actual columns, checked the same way `predictive_maintenance.preprocess()` already does it (`if col_name and col_name not in df.columns: raise ValueError(...)`).

### Scope note (read before starting)

**Downtime Risk is deliberately scoped down from the original design-spec vision.** The spec described "predict risk of an unplanned incident next shift" - a time-series, per-machine rolling-window classification problem structurally identical to Predictive Maintenance's rare-event framing. Building that now would require the same rolling-window featurization AND the anomaly-detection rare-event routing that's explicitly out of scope for this plan (that's a separate, larger piece of future work). This plan instead ships a simpler, still-useful v1: **predict how long a downtime incident of a given machine/reason/shift profile is likely to last** (regression on `duration_min`) - directly useful for staffing/scheduling decisions, reuses the exact same shared-regression pattern as Scrap Prediction, and doesn't invent time-series complexity this plan isn't scoped to solve well. If the fuller "predict before it happens" framing is wanted later, it's a new, separate plan - not a fix to this one.

---

## File Structure

New:
- `backend/app/services/automl_templates/_shared.py` - `make_simple_regression_preprocess()` / `make_simple_regression_postprocess()` factory functions, shared by both templates below.
- `backend/app/services/automl_templates/scrap_prediction.py` - roles + template registration for Scrap Prediction, built from the shared factories.
- `backend/app/services/automl_templates/downtime_risk.py` - roles + template registration for Downtime Risk, built from the shared factories.
- `backend/tests/test_shared_regression_template.py` - unit tests for the shared factory functions.
- `backend/tests/test_scrap_prediction_template.py` - Scrap Prediction preprocess/postprocess/end-to-end tests.
- `backend/tests/test_downtime_risk_template.py` - Downtime Risk preprocess/postprocess/end-to-end tests.

Modified:
- `backend/app/services/automl_templates/__init__.py` - two more bottom-of-file self-registering imports (`scrap_prediction`, `downtime_risk`), same pattern as the existing `predictive_maintenance` import.

---

### Task 1: Shared simple-regression template helper

**Files:**
- Create: `backend/app/services/automl_templates/_shared.py`
- Test: `backend/tests/test_shared_regression_template.py`

**Interfaces:**
- Consumes: `PreprocessResult` (existing, `backend/app/services/automl_templates/__init__.py`).
- Produces: `make_simple_regression_preprocess(*, target_role: str, required_roles: list[str], excluded_from_features: list[str] = (), explicit_feature_roles: list[str] | None = None, min_rows: int = 20) -> Callable[[pd.DataFrame, dict], PreprocessResult]`. `make_simple_regression_postprocess(*, summary_key: str) -> Callable[[dict | None, dict, dict], dict]` - the returned `postprocess()` raises `RuntimeError` if called with `engine_result=None` (these templates never set `skip_ml=True`, so a `None` engine_result would mean a caller bug, not a legitimate no-signal case).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_shared_regression_template.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "units": [100, 110, 90, 105, 95, 120, 80, 100, 115, 90] * 3,
        "scrap": [2, 3, 1, 4, 2, 5, 1, 2, 3, 2] * 3,
        "reason": ["tooling", "tooling", "cal", "tooling", "cal", "tooling", "cal", "tooling", "cal", "tooling"] * 3,
        "machine": ["M1", "M2"] * 15,
        "speed": [50, 55, 45, 52, 48, 60, 40, 50, 58, 47] * 3,
    })


def test_preprocess_all_other_columns_mode_uses_every_unmapped_column_as_a_feature():
    df = _df()
    preprocess = make_simple_regression_preprocess(
        target_role="scrap_col", required_roles=["units_col", "scrap_col"],
        excluded_from_features=["units_col", "reason_col"],
    )
    config = {"column_map": {"units_col": "units", "scrap_col": "scrap", "reason_col": "reason", "machine_col": "machine"}}
    result = preprocess(df, config)
    assert result.skip_ml is False
    assert result.task == "regression"
    assert result.target_col == "scrap"
    # "units" and "reason" excluded; "machine" and "speed" (unmapped by any
    # role at all) both survive since explicit_feature_roles=None means "all
    # other columns."
    assert set(result.feature_cols) == {"machine", "speed"}
    assert len(result.df) == 30


def test_preprocess_explicit_feature_roles_mode_uses_only_named_columns():
    df = pd.DataFrame({
        "dur": [12.0, 45.0, 8.0, 30.0, 15.0, 50.0, 10.0, 22.0, 18.0, 40.0] * 3,
        "machine": ["M1", "M2"] * 15,
        "reason": ["breakdown", "changeover"] * 15,
        "shift": ["day", "night"] * 15,
        "unrelated": ["x"] * 30,
    })
    preprocess = make_simple_regression_preprocess(
        target_role="duration_col", required_roles=["machine_col", "reason_col", "shift_col", "duration_col"],
        explicit_feature_roles=["machine_col", "reason_col", "shift_col"],
    )
    config = {"column_map": {"duration_col": "dur", "machine_col": "machine", "reason_col": "reason", "shift_col": "shift"}}
    result = preprocess(df, config)
    assert set(result.feature_cols) == {"machine", "reason", "shift"}
    assert "unrelated" not in result.feature_cols


def test_preprocess_raises_on_missing_required_role():
    df = _df()
    preprocess = make_simple_regression_preprocess(target_role="scrap_col", required_roles=["units_col", "scrap_col"])
    with pytest.raises(ValueError, match="Missing required column mapping"):
        preprocess(df, {"column_map": {"units_col": "units"}})


def test_preprocess_raises_on_mapped_column_not_in_dataset():
    df = _df()
    preprocess = make_simple_regression_preprocess(target_role="scrap_col", required_roles=["units_col", "scrap_col"])
    with pytest.raises(ValueError, match="not found in dataset"):
        preprocess(df, {"column_map": {"units_col": "units", "scrap_col": "does_not_exist"}})


def test_preprocess_raises_when_too_few_rows_have_a_valid_target():
    df = pd.DataFrame({"units": [100, 110, 90], "scrap": [2, None, 1]})
    preprocess = make_simple_regression_preprocess(
        target_role="scrap_col", required_roles=["units_col", "scrap_col"], min_rows=20,
    )
    with pytest.raises(ValueError, match="Not enough rows"):
        preprocess(df, {"column_map": {"units_col": "units", "scrap_col": "scrap"}})


def test_postprocess_summarizes_engine_result_under_summary_key():
    postprocess = make_simple_regression_postprocess(summary_key="scrap_summary")
    engine_result = {
        "pipeline": "fake-pipeline", "explain_X": "fake-x", "explain_y": "fake-y",
        "leaderboard": [{"rank": 1}],
        "meta": {"model_name": "Ridge", "metric_name": "rmse", "metric_value": 1.23, "rows_used": 42},
    }
    out = postprocess(engine_result, {}, {"target_col": "scrap"})
    assert out["scrap_summary"] == {
        "target_column": "scrap", "best_model": "Ridge", "metric_name": "rmse",
        "metric_value": 1.23, "rows_used": 42,
    }
    assert out["pipeline"] == "fake-pipeline"
    assert out["best_model_name"] == "Ridge"


def test_postprocess_raises_if_engine_result_is_none():
    postprocess = make_simple_regression_postprocess(summary_key="x")
    with pytest.raises(RuntimeError, match="never set skip_ml"):
        postprocess(None, {}, {"target_col": "y"})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_shared_regression_template.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_templates._shared'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/automl_templates/_shared.py`:

```python
"""Shared helpers for simple row-level regression templates (Scrap
Prediction, Downtime Risk) - templates with no bespoke legacy math to
preserve: map columns, predict a numeric target from the rest, summarize
the winning model. Predictive Maintenance does NOT use this - it has real
legacy RUL/cost/statistical-fallback math that needs its own preprocess/
postprocess (see automl_templates/predictive_maintenance.py).
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from app.services.automl_templates import PreprocessResult


def make_simple_regression_preprocess(
    *,
    target_role: str,
    required_roles: list[str],
    excluded_from_features: list[str] = (),
    explicit_feature_roles: list[str] | None = None,
    min_rows: int = 20,
) -> Callable[[pd.DataFrame, dict[str, Any]], PreprocessResult]:
    """Returns a preprocess() that predicts the numeric column mapped to
    `target_role` from either every other unmapped dataset column
    (explicit_feature_roles=None) or exactly the columns mapped to
    `explicit_feature_roles`. `excluded_from_features` names roles (e.g. a
    column that only exists once the outcome already happened - leakage)
    to keep out of the "all other columns" feature set; ignored in
    explicit-roles mode, where the feature set is already an exact list."""

    def preprocess(df: pd.DataFrame, config: dict[str, Any]) -> PreprocessResult:
        cols = config.get("column_map", {})
        missing = [r for r in required_roles if not cols.get(r)]
        if missing:
            raise ValueError(f"Missing required column mapping for: {', '.join(missing)}")
        for role_key, col_name in cols.items():
            if col_name and col_name not in df.columns:
                raise ValueError(f"Mapped column '{col_name}' (role '{role_key}') not found in dataset")

        target_col = cols[target_role]

        if explicit_feature_roles is not None:
            feature_cols = [cols[r] for r in explicit_feature_roles if cols.get(r)]
        else:
            excluded_cols = {cols[r] for r in excluded_from_features if cols.get(r)} | {target_col}
            feature_cols = [c for c in df.columns if c not in excluded_cols]

        if not feature_cols:
            raise ValueError("No usable feature columns remain after excluding target/leakage columns")

        work = df[[target_col, *feature_cols]].copy()
        work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
        work = work.dropna(subset=[target_col])
        if len(work) < min_rows:
            raise ValueError(
                f"Not enough rows with a valid '{target_col}' value ({len(work)}, need >= {min_rows})"
            )

        return PreprocessResult(
            skip_ml=False,
            df=work,
            target_col=target_col,
            task="regression",
            feature_cols=feature_cols,
            notes=[f"Predicting '{target_col}' from {len(feature_cols)} feature column(s) across {len(work)} rows."],
            context={"target_col": target_col},
        )

    return preprocess


def make_simple_regression_postprocess(
    *, summary_key: str,
) -> Callable[[dict[str, Any] | None, dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """Returns a postprocess() that summarizes the winning model into a
    small `{summary_key: {...}}` dict - no bespoke domain math, since these
    templates have no legacy behavior to preserve."""

    def postprocess(
        engine_result: dict[str, Any] | None, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        if engine_result is None:
            raise RuntimeError(
                "Simple regression templates never set skip_ml=True, so postprocess should always receive an engine_result"
            )
        meta = engine_result["meta"]
        summary = {
            "target_column": context["target_col"],
            "best_model": meta["model_name"],
            "metric_name": meta["metric_name"],
            "metric_value": meta["metric_value"],
            "rows_used": meta["rows_used"],
        }
        return {
            summary_key: summary,
            "pipeline": engine_result["pipeline"],
            "explain_X": engine_result["explain_X"],
            "explain_y": engine_result["explain_y"],
            "leaderboard": engine_result["leaderboard"],
            "best_model_name": meta["model_name"],
            "metric_name": meta["metric_name"],
            "metric_value": meta["metric_value"],
        }

    return postprocess
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_shared_regression_template.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/automl_templates/_shared.py backend/tests/test_shared_regression_template.py
git commit -m "feat: add shared simple-regression AutoML template helper"
```

---

### Task 2: Scrap Prediction template

**Files:**
- Create: `backend/app/services/automl_templates/scrap_prediction.py`
- Modify: `backend/app/services/automl_templates/__init__.py`
- Test: `backend/tests/test_scrap_prediction_template.py`

**Interfaces:**
- Consumes: `make_simple_regression_preprocess`/`make_simple_regression_postprocess` (Task 1); `ColumnRole`, `AutoMLTemplate`, `register_template` (existing registry).
- Produces: template id `"scrap_prediction"` registered and reachable via the existing `GET /automl/templates` and `POST /automl/templates/scrap_prediction/runs` routes (no route code changes needed - those routes already work for any registered template).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_scrap_prediction_template.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import scrap_prediction as sp

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _fixture_df() -> pd.DataFrame:
    rows = []
    for i in range(40):
        speed = 40 + (i % 8) * 5
        temp = 60 + (i % 5) * 3
        units = 100
        # Higher speed/temp -> more scrap, so there's real signal to learn.
        scrap = max(0, round((speed - 40) * 0.15 + (temp - 60) * 0.1))
        rows.append({
            "date": f"2026-01-{(i % 28) + 1:02d}", "machine": "M1" if i % 2 == 0 else "M2",
            "total_units": units, "scrap_units": scrap, "reason": "tooling" if scrap > 0 else "",
            "speed": speed, "temp": temp,
        })
    return pd.DataFrame(rows)


def test_scrap_prediction_registered():
    from app.services import automl_templates as templates
    ids = {t.id for t in templates.list_templates()}
    assert "scrap_prediction" in ids


def test_preprocess_excludes_total_units_and_reason_as_features():
    df = _fixture_df()
    config = {"column_map": {
        "total_units_col": "total_units", "scrap_col": "scrap_units",
        "reason_col": "reason", "machine_col": "machine", "date_col": "date",
    }}
    result = sp.preprocess(df, config)
    assert result.skip_ml is False
    assert result.target_col == "scrap_units"
    assert "total_units" not in result.feature_cols
    assert "reason" not in result.feature_cols
    assert {"machine", "date", "speed", "temp"} <= set(result.feature_cols)


def test_preprocess_only_requires_total_units_and_scrap():
    df = _fixture_df()
    config = {"column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units"}}
    result = sp.preprocess(df, config)
    assert result.skip_ml is False


def test_postprocess_produces_scrap_summary():
    df = _fixture_df()
    config = {"column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units", "machine_col": "machine"}}
    pre = sp.preprocess(df, config)
    from app.services import automl_engine
    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, thoroughness="fast",
    )
    out = sp.postprocess(engine_result, config, pre.context)
    assert out["scrap_summary"]["target_column"] == "scrap_units"
    assert out["pipeline"] is not None


@pytest.mark.asyncio
async def test_create_scrap_prediction_run_end_to_end(client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    df = _fixture_df()
    csv_bytes = df.to_csv(index=False).encode()
    up = await client.post("/datasets", headers=headers, files={"file": ("scrap.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "Scrap run", "dataset_id": dataset_id,
        "column_map": {"total_units_col": "total_units", "scrap_col": "scrap_units", "machine_col": "machine"},
        "config": {},
    }
    created = await client.post("/automl/templates/scrap_prediction/runs", headers=headers, json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["meta"]["scrap_summary"]["target_column"] == "scrap_units"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_scrap_prediction_template.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_templates.scrap_prediction'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/automl_templates/scrap_prediction.py`:

```python
"""Scrap Prediction AutoML template: predicts scrap/defect units from
whatever process-parameter columns exist in the dataset alongside the
mapped total-units/scrap columns. No legacy ML behavior to preserve -
scrap.py is pure SQL aggregation - so this is a thin wrapper over the
shared simple-regression template helper.
"""
from __future__ import annotations

from app.services.automl_templates import AutoMLTemplate, ColumnRole, register_template
from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess

ROLES = [
    ColumnRole("total_units_col", "Total units"),
    ColumnRole("scrap_col", "Scrap units"),
    ColumnRole("reason_col", "Rejection reason column", required=False),
    ColumnRole("machine_col", "Machine column", required=False),
    ColumnRole("date_col", "Date column", required=False),
]

# total_units_col is excluded from features (it's a normalization
# denominator, not a predictor) and reason_col is excluded because it only
# has a value once scrap already happened - using it as a feature would be
# leakage. Every other column in the dataset (machine, date, and any
# process-parameter columns the user didn't explicitly map) becomes a
# candidate feature.
preprocess = make_simple_regression_preprocess(
    target_role="scrap_col",
    required_roles=["total_units_col", "scrap_col"],
    excluded_from_features=["total_units_col", "reason_col"],
)

postprocess = make_simple_regression_postprocess(summary_key="scrap_summary")

register_template(AutoMLTemplate(
    id="scrap_prediction",
    label="Scrap Prediction",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=False,
))
```

- [ ] **Step 4: Register the module for import-time side effect**

Edit `backend/app/services/automl_templates/__init__.py`, find the existing bottom-of-file line:
```python
from app.services.automl_templates import predictive_maintenance  # noqa: E402,F401 - registers the template
```
Add immediately after it:
```python
from app.services.automl_templates import scrap_prediction  # noqa: E402,F401 - registers the template
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_scrap_prediction_template.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/automl_templates/scrap_prediction.py backend/app/services/automl_templates/__init__.py backend/tests/test_scrap_prediction_template.py
git commit -m "feat: add Scrap Prediction AutoML template"
```

---

### Task 3: Downtime Risk template

**Files:**
- Create: `backend/app/services/automl_templates/downtime_risk.py`
- Modify: `backend/app/services/automl_templates/__init__.py`
- Test: `backend/tests/test_downtime_risk_template.py`

**Interfaces:**
- Consumes: `make_simple_regression_preprocess`/`make_simple_regression_postprocess` (Task 1).
- Produces: template id `"downtime_risk"` registered, same generic-route reachability as Task 2.

**Context on the data shape:** `backend/app/services/downtime_tracking.py`'s `_materialize()` writes closed incidents as a CSV with columns `machine, reason, planned, shift, duration_min, started_at, ended_at`. This template predicts `duration_min` from `machine`/`reason`/`shift` - do NOT use `planned` as a feature or target: it's a deterministic function of `reason` (see `REASON_PLANNED` dict in that file), so including it anywhere would be tautological, not predictive.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downtime_risk_template.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from app.services.automl_templates import downtime_risk as dr

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

_REASONS = ["breakdown", "changeover", "material_shortage", "planned_maintenance", "quality_hold", "other"]
_BASE_DURATION = {"breakdown": 90, "changeover": 20, "material_shortage": 45, "planned_maintenance": 60, "quality_hold": 15, "other": 30}


def _fixture_df() -> pd.DataFrame:
    rows = []
    for i in range(40):
        reason = _REASONS[i % len(_REASONS)]
        shift = "day" if i % 2 == 0 else "night"
        # Night shift runs a bit longer on average - real, learnable signal.
        duration = _BASE_DURATION[reason] + (10 if shift == "night" else 0) + (i % 3)
        rows.append({
            "machine": "M1" if i % 3 == 0 else "M2", "reason": reason, "shift": shift,
            "duration_min": duration, "planned": reason in ("changeover", "planned_maintenance"),
        })
    return pd.DataFrame(rows)


def test_downtime_risk_registered():
    from app.services import automl_templates as templates
    ids = {t.id for t in templates.list_templates()}
    assert "downtime_risk" in ids


def test_preprocess_uses_exactly_machine_reason_shift_as_features():
    df = _fixture_df()
    config = {"column_map": {
        "machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min",
    }}
    result = dr.preprocess(df, config)
    assert result.skip_ml is False
    assert result.target_col == "duration_min"
    assert set(result.feature_cols) == {"machine", "reason", "shift"}
    assert "planned" not in result.feature_cols


def test_preprocess_requires_all_four_roles():
    df = _fixture_df()
    config = {"column_map": {"machine_col": "machine", "reason_col": "reason"}}
    with pytest.raises(ValueError, match="Missing required column mapping"):
        dr.preprocess(df, config)


def test_postprocess_produces_downtime_summary():
    df = _fixture_df()
    config = {"column_map": {"machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min"}}
    pre = dr.preprocess(df, config)
    from app.services import automl_engine
    engine_result = automl_engine.train_automl_baseline(
        pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
        feature_columns=pre.feature_cols, thoroughness="fast",
    )
    out = dr.postprocess(engine_result, config, pre.context)
    assert out["downtime_summary"]["target_column"] == "duration_min"
    assert out["pipeline"] is not None


@pytest.mark.asyncio
async def test_create_downtime_risk_run_end_to_end(client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    df = _fixture_df()
    csv_bytes = df.to_csv(index=False).encode()
    up = await client.post("/datasets", headers=headers, files={"file": ("downtime.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    body = {
        "name": "Downtime run", "dataset_id": dataset_id,
        "column_map": {"machine_col": "machine", "reason_col": "reason", "shift_col": "shift", "duration_col": "duration_min"},
        "config": {},
    }
    created = await client.post("/automl/templates/downtime_risk/runs", headers=headers, json=body)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    run = detail.json()
    assert run["status"] == "success", run.get("error")
    assert run["meta"]["downtime_summary"]["target_column"] == "duration_min"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_downtime_risk_template.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.automl_templates.downtime_risk'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/automl_templates/downtime_risk.py`:

```python
"""Downtime Risk AutoML template v1: predicts how long a downtime incident
of a given machine/reason/shift profile is likely to last, using the exact
column shape app/services/downtime_tracking.py's materialized live dataset
already produces (machine, reason, shift, duration_min).

Deliberately NOT "predict an incident before it happens" (the fuller
design-spec framing) - that needs rolling-window time-series featurization
and rare-event handling, out of scope here. See the plan's scope note.
"""
from __future__ import annotations

from app.services.automl_templates import AutoMLTemplate, ColumnRole, register_template
from app.services.automl_templates._shared import make_simple_regression_postprocess, make_simple_regression_preprocess

ROLES = [
    ColumnRole("machine_col", "Machine column"),
    ColumnRole("reason_col", "Downtime reason column"),
    ColumnRole("shift_col", "Shift column"),
    ColumnRole("duration_col", "Duration (minutes)"),
]

# Exactly machine/reason/shift as features - not "all other columns" mode,
# since a materialized downtime dataset's only other column (`planned`) is
# a deterministic function of `reason` and would be leakage/noise, not
# signal, if included.
preprocess = make_simple_regression_preprocess(
    target_role="duration_col",
    required_roles=["machine_col", "reason_col", "shift_col", "duration_col"],
    explicit_feature_roles=["machine_col", "reason_col", "shift_col"],
)

postprocess = make_simple_regression_postprocess(summary_key="downtime_summary")

register_template(AutoMLTemplate(
    id="downtime_risk",
    label="Downtime Risk",
    roles=ROLES,
    preprocess=preprocess,
    postprocess=postprocess,
    is_timeseries=False,
))
```

- [ ] **Step 4: Register the module for import-time side effect**

Edit `backend/app/services/automl_templates/__init__.py`, after the `scrap_prediction` import line added in Task 2, add:
```python
from app.services.automl_templates import downtime_risk  # noqa: E402,F401 - registers the template
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_downtime_risk_template.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/automl_templates/downtime_risk.py backend/app/services/automl_templates/__init__.py backend/tests/test_downtime_risk_template.py
git commit -m "feat: add Downtime Risk AutoML template"
```

---

### Task 4: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all passed, 0 failed, 0 errors (should be the prior full-suite count plus 17 new tests: 7 shared-helper + 5 scrap + 5 downtime).

- [ ] **Step 2: Confirm both templates are listed alongside predictive_maintenance**

Run: `cd backend && python -m pytest tests/test_automl_templates.py::test_list_templates_includes_predictive_maintenance tests/test_scrap_prediction_template.py::test_scrap_prediction_registered tests/test_downtime_risk_template.py::test_downtime_risk_registered -v`
Expected: 3 passed - all three templates coexist in the registry without interfering with each other.

- [ ] **Step 3: Confirm zero changes to the three protected files**

Run: `git diff --stat <task-1-base-sha>..HEAD -- backend/app/services/automl_engine.py backend/app/services/automl_runner.py backend/app/api/routes/automl.py`
Expected: no output (empty diff) - these files were not touched, confirming the "plug into existing infrastructure" design held.

- [ ] **Step 4: Report status**

Summarize: two more templates shipped (Scrap Prediction, Downtime Risk), both reusing the existing registry/runner/routes with zero changes to shared infrastructure, both accessible via the same generic `POST /automl/templates/{id}/runs` + `GET /automl/runs/{id}` flow the frontend `Predictive.tsx` page already demonstrates. No frontend UI was built for these two (out of scope per the plan) - they're API-only until a guided wizard frontend is built in a future plan.

## Self-Review Notes

**Spec coverage:** Implements the design spec's Scrap Prediction and Downtime Risk template *backends* only (roles, preprocess, postprocess, registration). Deliberately does not implement: the generic frontend wizard (`TemplatePicker`/`MappingWizard`/`ResultsView`), Downtime Risk's fuller "predict before it happens" time-series framing, or anything from Phases 2/3 (explainability, rare-event routing, data-quality cleaning) - all separate, larger pieces of work, not this plan's job.

**Placeholder scan:** No TBD/TODO markers. All code blocks are complete and runnable.

**Type consistency:** `make_simple_regression_preprocess`/`make_simple_regression_postprocess` (Task 1) are used identically by both `scrap_prediction.py` (Task 2) and `downtime_risk.py` (Task 3) - same parameter names, same return-callable contract. Both templates' `postprocess()` return dicts match exactly what `automl_runner.py`'s existing template branch already destructures (`pipeline`, `explain_X`, `explain_y`, `leaderboard`, `best_model_name`, `metric_name`, `metric_value`, plus one arbitrary summary key merged into `run.meta`) - the same contract `predictive_maintenance.postprocess()` already satisfies, so no runner changes are needed.
