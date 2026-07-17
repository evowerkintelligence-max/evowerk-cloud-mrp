# Batch AutoML Risk Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an `AlertRule` fire when a trained AutoML run's predictions on a dataset cross a threshold - "alert me when predicted scrap risk exceeds 80%" - reusing the existing alert-rule CRUD, scan flow, severity levels, and andon/webhook delivery unchanged.

**Architecture:** One new evaluator, `_eval_automl_risk`, added to `app/services/alerts.py` alongside the existing `_eval_scrap`/`_eval_quality`/etc. dataset evaluators. Unlike those (pure, synchronous DuckDB-only functions dispatched through `_DATASET_EVALUATORS`), this evaluator needs an async DB lookup (the `AutoMLRun` row) before it can score anything, so it's dispatched via its own branch in `evaluate_rule()` - the same way `stream_metric` already gets special-cased there, not forced into the sync dict pattern. No new table, no new route file, no new frontend - this is the "cheap" plan the live-scoring/drift plan explicitly is not.

**Tech Stack:** FastAPI, SQLAlchemy (async), the existing, unmodified `automl_engine.py` (only `joblib.load` + calling the already-fitted pipeline's `.predict()`/`.predict_proba()` directly, matching how `automl_runner.py` and `predictive_maintenance.postprocess()` already call the same pipeline).

## Global Constraints

- `automl_engine.py`, `automl_runner.py`, and every existing AutoML template file are NOT modified by this plan - this plan only reads an already-trained run's persisted pipeline (`run.model_path`) and metadata (`run.meta`, `run.task`, `run.target_column`), the same way the existing `/automl/runs/{id}/predict` route already does.
- The existing dataset evaluators' `AlertTrigger` shape, severity handling, `_offenders_suffix` formatting, and `_fire_webhook` delivery are reused exactly as-is - no changes to any of those.
- `AutoMLRun` lookups must be scoped by `org_id` (matching every other query against that table in this codebase).
- Test password policy: reuse `"correct-horse-battery-staple1"` with a trailing ` # pragma: allowlist secret` comment on standalone literal lines.

### Design note: why feature-column selection needs a fallback

`predictive_maintenance` and other AutoML templates (built in an earlier plan) do **not** populate `run.meta["feature_columns"]` the way a generic (non-template) AutoML run does - `automl_runner.py`'s template branch overwrites `run.meta` with the template's own summary dict (e.g. `{"predictive_result": {...}}`), which has no `feature_columns` key. `run.target_column` IS reliably set for both generic and template runs, though. This plan's evaluator must therefore mirror `automl_engine.predict_with_model()`'s own existing fallback: if `meta.get("feature_columns")` is empty, derive the feature set as "every dataset column except `run.target_column`" - the same fallback `predict_with_model()` already uses for exactly this situation, not a new invention.

---

## File Structure

Modified:
- `backend/app/services/alerts.py` - add `_eval_automl_risk()`, wire it into `evaluate_rule()`.
- `backend/app/api/routes/alerts.py` - add `"automl_risk"` to `_ALERT_TYPES`.

New:
- `backend/tests/test_automl_risk_alert.py` - unit tests for `_eval_automl_risk` (both a generic-run-style `meta["feature_columns"]` and a template-run-style fallback case) plus one end-to-end API test.

---

### Task 1: `_eval_automl_risk` evaluator

**Files:**
- Modify: `backend/app/services/alerts.py`
- Test: `backend/tests/test_automl_risk_alert.py`

**Interfaces:**
- Consumes: `AlertRule`, `AlertTrigger`, `_missing_columns`, `_offenders_suffix` (existing, `backend/app/services/alerts.py`); `AutoMLRun` (existing, `backend/app/models/automl_run.py`); `ds_service.dataframe_for` (existing, `backend/app/services/datasets.py`).
- Produces: `async def _eval_automl_risk(db: AsyncSession, org_id: uuid.UUID, rule: AlertRule, dataset: Dataset) -> AlertTrigger`. `rule.config` keys read: `run_id` (str, required), `threshold` (float, default 0.5), `comparison` ("gt" | "lt", default "gt"), `offender_col` (str, optional - a dataset column to group triggered rows by, e.g. a machine name column; falls back to a single `"All"` offender when omitted, matching the other evaluators' `group_col`/`machine_col` optional pattern). Also modifies `evaluate_rule()` to dispatch `rule.type == "automl_risk"` to this new function.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_automl_risk_alert.py`:

```python
from __future__ import annotations

import uuid

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.models.alert import AlertRule
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import alerts


def _fake_rule(config: dict) -> AlertRule:
    return AlertRule(
        id=uuid.uuid4(), org_id=uuid.uuid4(), created_by=uuid.uuid4(), name="Risk alert",
        type="automl_risk", severity="warning", enabled=True, dataset_id=uuid.uuid4(), config=config,
    )


def _fitted_pipeline_and_model_path(tmp_path, feature_cols: list[str]) -> str:
    X = pd.DataFrame({c: [0, 1, 0, 1, 0, 1, 0, 1] for c in feature_cols})
    y = [0, 1, 0, 1, 0, 1, 0, 1]
    pipe = Pipeline([("model", LogisticRegression())])
    pipe.fit(X, y)
    path = tmp_path / "model.joblib"
    joblib.dump(pipe, path)
    return str(path)


@pytest.mark.asyncio
async def test_eval_automl_risk_uses_meta_feature_columns_when_present(tmp_path, monkeypatch):
    model_path = _fitted_pipeline_and_model_path(tmp_path, ["x1", "x2"])
    run = AutoMLRun(
        id=uuid.uuid4(), org_id=uuid.uuid4(), dataset_id=uuid.uuid4(), created_by=uuid.uuid4(),
        name="r", task="classification", target_column="y", status="success", model_path=model_path,
        meta={"feature_columns": ["x1", "x2"], "task": "classification"},
    )
    rule = _fake_rule({"run_id": str(run.id), "threshold": 0.5, "offender_col": "machine"})
    # _missing_columns() checks offender_col against dataset.columns (a list
    # of {"name": ...} dicts), so this fixture must actually list "machine" -
    # an empty columns=[] would make the evaluator return early with a
    # "column not found" warning instead of exercising the scoring path.
    dataset = Dataset(
        id=uuid.uuid4(), org_id=run.org_id, name="d", original_filename="d.csv", fmt="csv", storage_ext="csv",
        created_by=uuid.uuid4(), row_count=4,
        columns=[{"name": "x1"}, {"name": "x2"}, {"name": "machine"}, {"name": "y"}],
    )

    df = pd.DataFrame({"x1": [1, 1, 0, 0], "x2": [1, 1, 0, 0], "machine": ["M1", "M2", "M3", "M4"], "y": [1, 1, 0, 0]})
    monkeypatch.setattr("app.services.datasets.dataframe_for", lambda ds: df)

    class _FakeResult:
        def scalar_one_or_none(self):
            return run

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), run.org_id, rule, dataset)
    assert trigger.type == "automl_risk"
    assert set(trigger.offenders) <= {"M1", "M2", "M3", "M4"}


@pytest.mark.asyncio
async def test_eval_automl_risk_falls_back_to_target_column_when_meta_has_no_feature_columns(tmp_path, monkeypatch):
    # Mirrors a template run: meta has no "feature_columns" key at all, only
    # run.target_column is reliably set (see the plan's design note).
    model_path = _fitted_pipeline_and_model_path(tmp_path, ["x1", "x2"])
    run = AutoMLRun(
        id=uuid.uuid4(), org_id=uuid.uuid4(), dataset_id=uuid.uuid4(), created_by=uuid.uuid4(),
        name="r", task="classification", target_column="y", status="success", model_path=model_path,
        meta={"predictive_result": {"marker": "ok"}},
    )
    rule = _fake_rule({"run_id": str(run.id), "threshold": 0.5})
    dataset = Dataset(id=uuid.uuid4(), org_id=run.org_id, name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=4, columns=[])

    df = pd.DataFrame({"x1": [1, 1, 0, 0], "x2": [1, 1, 0, 0], "y": [1, 1, 0, 0]})
    monkeypatch.setattr("app.services.datasets.dataframe_for", lambda ds: df)

    class _FakeResult:
        def scalar_one_or_none(self):
            return run

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), run.org_id, rule, dataset)
    # No crash, no "unknown column" error - feature set correctly fell back
    # to "every column except target_column" (x1, x2), not an empty set.
    assert trigger.type == "automl_risk"
    assert "Error" not in trigger.message


@pytest.mark.asyncio
async def test_eval_automl_risk_reports_missing_run_id():
    rule = _fake_rule({})
    dataset = Dataset(id=uuid.uuid4(), org_id=uuid.uuid4(), name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=0, columns=[])

    class _FakeDB:
        async def execute(self, *a, **kw):
            raise AssertionError("should not query the DB when run_id is missing")

    trigger = await alerts._eval_automl_risk(_FakeDB(), uuid.uuid4(), rule, dataset)
    assert trigger.triggered is False
    assert "No AutoML run configured" in trigger.message


@pytest.mark.asyncio
async def test_eval_automl_risk_reports_run_not_found():
    rule = _fake_rule({"run_id": str(uuid.uuid4())})
    dataset = Dataset(id=uuid.uuid4(), org_id=uuid.uuid4(), name="d", original_filename="d.csv", fmt="csv", storage_ext="csv", created_by=uuid.uuid4(), row_count=0, columns=[])

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, *a, **kw):
            return _FakeResult()

    trigger = await alerts._eval_automl_risk(_FakeDB(), uuid.uuid4(), rule, dataset)
    assert trigger.triggered is False
    assert "not found" in trigger.message.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_risk_alert.py -v`
Expected: FAIL with `AttributeError: module 'app.services.alerts' has no attribute '_eval_automl_risk'`

- [ ] **Step 3: Write the implementation**

In `backend/app/services/alerts.py`, add this function after `eval_stream_metric` (before the `# ─── Andon / relay output ───` section):

```python
async def _eval_automl_risk(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule, dataset: Dataset,
) -> AlertTrigger:
    """Scores a dataset against an already-trained AutoML run's persisted
    pipeline and flags rows above/below a threshold - the batch counterpart
    to eval_stream_metric's live-stream case. Needs an async AutoMLRun
    lookup unlike the other dataset evaluators (which are pure/sync), which
    is why this isn't dispatched through _DATASET_EVALUATORS - see
    evaluate_rule()."""
    import joblib

    from app.models.automl_run import AutoMLRun

    cfg = rule.config
    run_id = str(cfg.get("run_id") or "").strip()
    threshold = float(cfg.get("threshold", 0.5))
    comparison = cfg.get("comparison", "gt")
    offender_col = str(cfg.get("offender_col") or "").strip()

    if not run_id:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, "No AutoML run configured.")

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Invalid run_id: {run_id}")

    run = (
        await db.execute(select(AutoMLRun).where(AutoMLRun.id == run_uuid, AutoMLRun.org_id == org_id))
    ).scalar_one_or_none()
    if run is None or run.status != "success" or not run.model_path:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "AutoML run not found or has no trained model.",
        )

    if offender_col:
        col_warn = _missing_columns(dataset, {"offender": offender_col})
        if col_warn:
            return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, col_warn)

    try:
        df = ds_service.dataframe_for(dataset)
        pipe = joblib.load(run.model_path)
    except Exception as exc:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Error loading run: {exc}")

    meta = run.meta or {}
    feat_cols = [c for c in (meta.get("feature_columns") or []) if c in df.columns]
    if not feat_cols:
        # Template runs don't populate meta["feature_columns"] - fall back
        # to "every column except the target", the same fallback
        # automl_engine.predict_with_model() already uses for this case.
        target_col = run.target_column
        if target_col and target_col in df.columns:
            feat_cols = [c for c in df.columns if c != target_col]
        else:
            feat_cols = list(df.columns)

    X = df[feat_cols].copy() if feat_cols else df.copy()

    try:
        task = str(meta.get("task") or run.task or "").strip().lower()
        if task == "classification" and hasattr(pipe, "predict_proba"):
            proba = pipe.predict_proba(X)
            scores = [float(row[1]) if len(row) == 2 else float(max(row)) for row in proba]
        else:
            scores = [float(v) for v in pipe.predict(X)]
    except Exception as exc:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold, f"Error scoring dataset: {exc}")

    is_high = comparison != "lt"

    if offender_col and offender_col in df.columns:
        offender_values = df[offender_col].astype(str).tolist()
        offenders = sorted({o for o, s in zip(offender_values, scores) if (s > threshold if is_high else s < threshold)})
        value = max(scores) if is_high else min(scores)
    else:
        value = max(scores) if is_high else min(scores)
        offenders = ["All"] if (value > threshold if is_high else value < threshold) else []

    triggered = bool(offenders)
    op_word = "above" if is_high else "below"
    message = f"Predicted risk {value:.3f} {op_word} threshold {threshold:.3f}" + _offenders_suffix(offenders)
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(value, 4), threshold,
        message, offenders,
    )
```

Then modify `evaluate_rule()` (add the `automl_risk` branch right after the existing `stream_metric` check):

```python
async def evaluate_rule(
    db: AsyncSession, org_id: uuid.UUID, rule: AlertRule,
    *, dataset: Dataset | None = None, con=None, rel: str | None = None,
) -> AlertTrigger:
    if rule.type == "stream_metric":
        return eval_stream_metric(rule)
    if rule.type == "automl_risk":
        if rule.dataset_id is None:
            return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, "No dataset configured.")
        if dataset is None:
            dataset = await ds_service.get_dataset_or_404(db, org_id, rule.dataset_id)
        return await _eval_automl_risk(db, org_id, rule, dataset)
    evaluator = _DATASET_EVALUATORS.get(rule.type)
    if evaluator is None:
        raise ValueError(f"Unknown alert type: {rule.type}")
    if rule.dataset_id is None:
        return AlertTrigger(str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, 0.0, "No dataset configured.")
    if dataset is None:
        dataset = await ds_service.get_dataset_or_404(db, org_id, rule.dataset_id)
    if con is not None:
        return evaluator(rule, dataset, con, rel)
    con = ds_service.open_con()
    try:
        return evaluator(rule, dataset, con, ds_service.dataset_relation(dataset))
    finally:
        con.close()
```

(Only the new `if rule.type == "automl_risk":` block is new - everything below it is the existing function body, unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_risk_alert.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/alerts.py backend/tests/test_automl_risk_alert.py
git commit -m "feat: add _eval_automl_risk batch alert evaluator"
```

---

### Task 2: Register the alert type + end-to-end test

**Files:**
- Modify: `backend/app/api/routes/alerts.py`
- Test: `backend/tests/test_automl_risk_alert.py` (append)

**Interfaces:**
- Produces: `POST /alerts/rules` accepts `type="automl_risk"` (existing route, existing request/response models - only the `_ALERT_TYPES` allowlist tuple changes).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_automl_risk_alert.py`:

```python
PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_create_automl_risk_rule_and_scan_end_to_end(client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Reuse the Scrap Prediction template (already shipped) to get a real,
    # trained run to alert on - avoids duplicating a full training fixture
    # here just to get a model_path.
    rows = []
    for i in range(40):
        speed = 40 + (i % 8) * 5
        units, scrap = 100, max(0, round((speed - 40) * 0.2))
        rows.append(f"M{i % 2 + 1},{units},{scrap},{speed}")
    csv_bytes = ("machine,units,scrap,speed\n" + "\n".join(rows) + "\n").encode()

    up = await client.post("/datasets", headers=headers, files={"file": ("scrap.csv", csv_bytes, "text/csv")})
    assert up.status_code == 201
    dataset_id = up.json()["id"]

    created_run = await client.post(
        "/automl/templates/scrap_prediction/runs", headers=headers,
        json={
            "name": "Scrap risk model", "dataset_id": dataset_id,
            "column_map": {"total_units_col": "units", "scrap_col": "scrap", "machine_col": "machine"},
            "config": {},
        },
    )
    assert created_run.status_code == 202, created_run.text
    run_id = created_run.json()["id"]
    run_detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert run_detail.json()["status"] == "success"

    rule = await client.post(
        "/alerts/rules", headers=headers,
        json={
            "name": "High scrap risk", "type": "automl_risk", "severity": "warning",
            "dataset_id": dataset_id,
            "config": {"run_id": run_id, "threshold": -1e9, "offender_col": "machine"},
        },
    )
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    scan = await client.post(f"/alerts/scan/{rule_id}", headers=headers)
    assert scan.status_code == 200, scan.text
    result = scan.json()
    assert result["type"] == "automl_risk"
    # threshold set absurdly low so every predicted value trips it -
    # proves the whole pipeline (route -> evaluate_rule -> _eval_automl_risk
    # -> joblib.load -> pipe.predict) actually ran end-to-end.
    assert result["triggered"] is True
    assert "machine" not in result  # sanity: response is AlertTriggerResponse shape, not raw dict
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_automl_risk_alert.py::test_create_automl_risk_rule_and_scan_end_to_end -v`
Expected: FAIL with `400: Unknown alert type: automl_risk` (the route rejects the type before it ever reaches `evaluate_rule`)

- [ ] **Step 3: Add the alert type**

In `backend/app/api/routes/alerts.py`, change:
```python
_ALERT_TYPES = ("machine_downtime", "quality", "scrap", "inventory", "production_target", "stream_metric")
```
to:
```python
_ALERT_TYPES = ("machine_downtime", "quality", "scrap", "inventory", "production_target", "stream_metric", "automl_risk")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_automl_risk_alert.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/alerts.py backend/tests/test_automl_risk_alert.py
git commit -m "feat: allow automl_risk as an alert rule type"
```

---

### Task 3: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all passed, 0 failed, 0 errors (prior full-suite count plus 5 new tests).

- [ ] **Step 2: Confirm the existing alert types and stream_metric path are untouched**

Run: `cd backend && python -m pytest tests/ -k "alert" -v`
Expected: every existing alert test (machine_downtime, quality, scrap, inventory, production_target, stream_metric, rule CRUD) still passes alongside the 5 new `automl_risk` tests - if a pre-existing `tests/test_alerts.py` (or similarly named file) exists and any of its tests fail, that's this task's own regression to fix, not a pre-existing issue to ignore.

- [ ] **Step 3: Confirm `automl_engine.py`/`automl_runner.py` are untouched**

Run: `git diff --stat <task-1-base-sha>..HEAD -- backend/app/services/automl_engine.py backend/app/services/automl_runner.py`
Expected: no output.

- [ ] **Step 4: Report status**

Summarize: `automl_risk` alert rules can now be created via the existing `POST /alerts/rules` route and scanned via the existing `POST /alerts/scan`/`POST /alerts/scan/{rule_id}` routes, reusing every existing alert-rule capability (severity, andon/relay webhook delivery via `_fire_webhook`, enable/disable) unchanged. No live-stream wiring - that remains deferred to the (larger, not-yet-scoped) real-time scoring plan.

## Self-Review Notes

**Spec coverage:** Implements the design spec's "batch case" alerting exactly (`_eval_automl_risk`, reusing `AlertRule`/`_fire_webhook` as-is). Deliberately does not implement the "live case" (publishing a score into a `RealtimeStream`'s metrics dict) - that depends on real-time scoring infrastructure this session explicitly deferred as its own, larger plan.

**Placeholder scan:** No TBD/TODO markers.

**Type consistency:** `_eval_automl_risk`'s signature (`db, org_id, rule, dataset`) matches how `evaluate_rule()` calls it in Task 1. The `AlertTrigger` positional-argument order used throughout (`rule_id, rule_name, type, severity, triggered, value, threshold, message, offenders=[]`) matches the existing dataclass definition and every other evaluator's construction calls already in the file - no new shape introduced.
