# Predictive Maintenance Anomaly Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `predictive_maintenance.py`'s reliance on `legacy._stat_analyze()` (a hand-tuned heuristic) with real unsupervised anomaly detection (`sklearn.ensemble.IsolationForest`) for machines that have enough rolling-window history to establish a "normal" baseline but not enough labeled failures to train the AutoML classifier.

**Architecture:** A new middle branch is inserted into `postprocess()`'s per-machine scoring: classifier (enough labeled failures) -> anomaly detection (some history, no/too-few labeled failures) -> statistical heuristic (almost no history at all). `preprocess()`'s already-computed `training_df` (currently discarded when `has_ml_signal()` is false) is threaded through `context` so `postprocess()` can fit an `IsolationForest` on it regardless of whether the classifier ran.

**Tech Stack:** `sklearn.ensemble.IsolationForest` (already a dependency via `scikit-learn>=1.4.0` in `backend/requirements.txt` - no new package), pandas/numpy (existing).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-17-predictive-maintenance-anomaly-detection-design.md`.
- Scope is `backend/app/services/automl_templates/predictive_maintenance.py` and its test file only - `scrap_prediction.py`/`downtime_risk.py` have no statistical-fallback concept and are untouched.
- No new dependency - `IsolationForest` is already importable from the installed `scikit-learn`.
- `_MIN_ANOMALY_ROWS = 15`: below this many total rolling-window rows, `legacy._stat_analyze()` remains the last resort (IsolationForest can't establish a meaningful baseline on a handful of rows).
- No changes to `PredictiveResult`/`MachineRisk`/`ModelMeta` dataclasses, no API changes, no frontend changes, no migration. `model_meta` stays `None` whenever no classifier trained (unchanged from today).
- `ml_powered` becomes `True` for anomaly-scored machines - it is genuinely model-scored now, and it's the only field of this branch `Predictive.tsx` reads (via `model_meta`'s presence, not `ml_powered`, for its algorithm badge), so this is safe and additive.
- Test password/fixture conventions: reuse the existing CSV-construction style already in `backend/tests/test_predictive_maintenance_template.py` (plain `pd.read_csv(StringIO(...))` fixtures, no new test helpers).

---

## File Structure

Modified only:
- `backend/app/services/automl_templates/predictive_maintenance.py` - add `_MIN_ANOMALY_ROWS` constant, carry `training_df` through `context`, add `_fit_anomaly_model()`/`_anomaly_score()` helpers, wire an `elif` branch into `postprocess()`.
- `backend/tests/test_predictive_maintenance_template.py` - append tests for the new context field, the two new helper functions, and the end-to-end anomaly-detection path through `postprocess()`.

No new files.

---

### Task 1: Carry `training_df` through `context`, add the anomaly floor constant

**Files:**
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py:16` (constants), `:124-129` (context dict in `preprocess()`)
- Test: `backend/tests/test_predictive_maintenance_template.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `predictive_maintenance._MIN_ANOMALY_ROWS: int = 15`; `PreprocessResult.context["training_df"]: pd.DataFrame` - present on every `preprocess()` call regardless of `skip_ml`, used by Task 3's `_fit_anomaly_model()`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_predictive_maintenance_template.py`:

```python
def test_context_carries_training_df_regardless_of_ml_signal():
    df = _fixture_df()
    config = {
        "column_map": {
            "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
            "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
            "failure_col": "failure", "downtime_category_col": "downtime_category",
        },
    }
    result = pm.preprocess(df, config)
    assert "training_df" in result.context
    assert len(result.context["training_df"]) == len(result.df)

    short_rows = [f"2026-01-{day:02d},M1,480,470,920,900," for day in range(1, 9)]
    from io import StringIO
    short_df = pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(short_rows) + "\n"))
    short_config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
    }}
    short_result = pm.preprocess(short_df, short_config)
    assert short_result.skip_ml is True
    assert "training_df" in short_result.context
    assert len(short_result.context["training_df"]) == 0


def test_min_anomaly_rows_constant_is_below_classifier_floor():
    assert pm._MIN_ANOMALY_ROWS < pm._MIN_ROWS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k "training_df or min_anomaly"`
Expected: FAIL - `test_context_carries_training_df_regardless_of_ml_signal` fails with `AssertionError` (`'training_df' in result.context` is `False`), `test_min_anomaly_rows_constant_is_below_classifier_floor` fails with `AttributeError: module ... has no attribute '_MIN_ANOMALY_ROWS'`.

- [ ] **Step 3: Add the constant and carry `training_df` through context**

In `backend/app/services/automl_templates/predictive_maintenance.py`, change line 16:

```python
_W, _H, _MIN_ROWS = 7, 3, 40
```

to:

```python
_W, _H, _MIN_ROWS, _MIN_ANOMALY_ROWS = 7, 3, 40, 15
```

Then change the `context` dict (lines 124-129) from:

```python
    context = {
        "grouped": grouped, "daily_ml": daily_ml, "days_per_period": days_per_period,
        "last_x": last_x, "column_map": cols, "hourly_value": config.get("hourly_value", 1000.0),
        "n_training_rows": len(training_df), "df": df_ready,
        "failure_col": cols.get("failure_col"), "downtime_category_col": cols.get("downtime_category_col"),
    }
```

to:

```python
    context = {
        "grouped": grouped, "daily_ml": daily_ml, "days_per_period": days_per_period,
        "last_x": last_x, "column_map": cols, "hourly_value": config.get("hourly_value", 1000.0),
        "n_training_rows": len(training_df), "df": df_ready, "training_df": training_df,
        "failure_col": cols.get("failure_col"), "downtime_category_col": cols.get("downtime_category_col"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k "training_df or min_anomaly"`
Expected: 2 passed

- [ ] **Step 5: Run the full existing template test suite to confirm no regressions**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: all passed (this step only adds a dict key and a constant - no existing behavior changes)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: carry training_df through predictive-maintenance context for anomaly detection"
```

---

### Task 2: `_fit_anomaly_model()` and `_anomaly_score()` helpers

**Files:**
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py` (add imports, add helpers between `_machine_risk_from_prob` and `postprocess`, i.e. after line 177)
- Test: `backend/tests/test_predictive_maintenance_template.py` (append)

**Interfaces:**
- Consumes: `legacy._ML_FEATURES` (existing, `backend/app/services/predictive.py:22`, the 6 feature names in fixed order), `legacy._ML_FEAT_LABELS` (existing, `predictive.py:23-29`), `_MIN_ANOMALY_ROWS` (Task 1).
- Produces: `_fit_anomaly_model(training_df: pd.DataFrame | None) -> tuple[IsolationForest, np.ndarray, np.ndarray] | None` (returns `None` when `training_df` is `None` or has fewer than `_MIN_ANOMALY_ROWS` rows; otherwise `(model, mean, std)` where `mean`/`std` are per-feature arrays over `legacy._ML_FEATURES`, in that column order). `_anomaly_score(model, mean, std, x_row: list[float]) -> tuple[float, list[dict]]` - `x_row` must be a 6-element list in `legacy._ML_FEATURES` order (the same shape `context["last_x"][machine]` already produces); returns `(failure_probability, drivers)` where `drivers` matches the `{feature, label, importance, elevated}` shape `legacy._stat_analyze()`'s contributions already use.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_predictive_maintenance_template.py`:

```python
def test_fit_anomaly_model_returns_none_below_floor():
    training_df = pd.DataFrame({f: [0.5] * 10 for f in pm.legacy._ML_FEATURES})
    assert pm._fit_anomaly_model(training_df) is None
    assert pm._fit_anomaly_model(None) is None


def test_fit_anomaly_model_returns_fitted_model_at_or_above_floor():
    training_df = pd.DataFrame({f: [0.5] * pm._MIN_ANOMALY_ROWS for f in pm.legacy._ML_FEATURES})
    fitted = pm._fit_anomaly_model(training_df)
    assert fitted is not None
    model, mean, std = fitted
    assert mean.shape == (6,)
    assert std.shape == (6,)
    assert (std > 0).all()  # zero-variance columns must be guarded, not left at 0


def test_anomaly_score_flags_low_oee_and_high_scrap_as_top_drivers():
    training_df = pd.DataFrame({
        "oee_7d": [0.78, 0.79, 0.80, 0.81, 0.82] * 4,
        "oee_slope_7d": [0.0] * 20,
        "scrap_rate_7d": [0.09, 0.10, 0.11, 0.10, 0.09] * 4,
        "dt_rate_7d": [0.1] * 20,
        "fail_count_7d": [0.0] * 20,
        "days_since_fail": [60.0] * 20,
    })
    fitted = pm._fit_anomaly_model(training_df)
    assert fitted is not None
    model, mean, std = fitted

    bad_row = [0.3, -0.05, 0.6, 0.1, 0.0, 60.0]  # far lower OEE, far higher scrap than baseline
    prob, drivers = pm._anomaly_score(model, mean, std, bad_row)
    assert 0.0 <= prob <= 1.0
    assert prob > 0.5  # this row is a clear outlier vs. the tight baseline
    feature_names = {d["feature"] for d in drivers}
    assert "oee_7d" in feature_names
    assert "scrap_rate_7d" in feature_names
    for d in drivers:
        assert 0.0 <= d["importance"] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k "fit_anomaly_model or anomaly_score"`
Expected: FAIL with `AttributeError: module ... has no attribute '_fit_anomaly_model'`

- [ ] **Step 3: Add imports**

In `backend/app/services/automl_templates/predictive_maintenance.py`, change lines 6-14 from:

```python
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.services import predictive as legacy
from app.services.automl_templates import AutoMLTemplate, ColumnRole, PreprocessResult, register_template
```

to:

```python
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.services import predictive as legacy
from app.services.automl_templates import AutoMLTemplate, ColumnRole, PreprocessResult, register_template
```

- [ ] **Step 4: Write the helpers**

Insert immediately after `_machine_risk_from_prob` (after line 177's closing `}, risk` and before `def postprocess(`):

```python
_BADNESS_DIRECTION = {
    "oee_7d": "low", "oee_slope_7d": "low", "scrap_rate_7d": "high",
    "dt_rate_7d": "high", "fail_count_7d": "high", "days_since_fail": "low",
}


def _fit_anomaly_model(training_df: pd.DataFrame | None) -> tuple[IsolationForest, np.ndarray, np.ndarray] | None:
    """Fits an unsupervised baseline over the same rolling-window features
    used to train the classifier, for machines that don't have enough
    labeled failures for supervised training but do have enough history to
    define what "normal" looks like. Returns None below _MIN_ANOMALY_ROWS -
    too few rows to trust a baseline."""
    if training_df is None or len(training_df) < _MIN_ANOMALY_ROWS:
        return None
    x = training_df[legacy._ML_FEATURES].to_numpy(dtype=float)
    model = IsolationForest(n_estimators=100, contamination=0.1, random_state=0).fit(x)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    return model, mean, std


def _anomaly_score(
    model: IsolationForest, mean: np.ndarray, std: np.ndarray, x_row: list[float],
) -> tuple[float, list[dict]]:
    """Scores one machine's latest rolling window against the fitted
    baseline. IsolationForest.decision_function() is positive for inliers,
    negative for outliers, unbounded - squashed into the same 0-1
    failure_probability scale the classifier and statistical fallback both
    already use."""
    x = np.asarray(x_row, dtype=float).reshape(1, -1)
    decision = float(model.decision_function(x)[0])
    prob = round(min(0.99, max(0.01, 1.0 / (1.0 + math.exp(5.0 * decision)))), 3)

    z = (x[0] - mean) / std
    contributions = []
    for idx, feature in enumerate(legacy._ML_FEATURES):
        badness = -z[idx] if _BADNESS_DIRECTION[feature] == "low" else z[idx]
        badness = max(0.0, badness)
        if badness < 0.1:
            continue
        contributions.append({
            "feature": feature, "label": legacy._ML_FEAT_LABELS[feature],
            "importance": round(min(1.0, badness / 3.0), 3), "elevated": badness > 1.5,
        })
    contributions.sort(key=lambda d: d["importance"], reverse=True)
    return prob, contributions[:3]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k "fit_anomaly_model or anomaly_score"`
Expected: 3 passed

- [ ] **Step 6: Run the full existing template test suite to confirm no regressions**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: add IsolationForest anomaly scoring helpers for predictive maintenance"
```

---

### Task 3: Wire anomaly detection into `postprocess()`

**Files:**
- Modify: `backend/app/services/automl_templates/predictive_maintenance.py:187` (insert), `:241-254` (branch)
- Test: `backend/tests/test_predictive_maintenance_template.py` (append)

**Interfaces:**
- Consumes: `_fit_anomaly_model` (Task 2), `_anomaly_score` (Task 2), `context["training_df"]` (Task 1).
- Produces: `postprocess()` now scores any machine not covered by the classifier through anomaly detection when `context["training_df"]` has `>= _MIN_ANOMALY_ROWS` rows and the machine has a `last_x` snapshot; otherwise falls through to `legacy._stat_analyze()` exactly as before. No change to `postprocess()`'s return shape.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_predictive_maintenance_template.py`:

```python
def _anomaly_fixture_df() -> pd.DataFrame:
    # M1: healthy (OEE ~0.96) for 25 days, then a 5-day degradation (OEE
    # ~0.16) with no failure/downtime_category column mapped at all - so
    # daily_ml["is_failure"] stays 0 throughout and is_failure_next is 0 for
    # every training row (0% positive rate fails has_ml_signal's 5-90% band,
    # forcing skip_ml=True), while still producing >= _MIN_ANOMALY_ROWS
    # rolling windows (n=30, W=7, H=3 -> range(7, 27) = 20 rows).
    from io import StringIO
    rows = []
    for day in range(1, 26):
        rows.append(f"2026-01-{day:02d},M1,480,470,920,900,")
    for day in range(26, 31):
        rows.append(f"2026-01-{day:02d},M1,480,150,200,150,")
    return pd.read_csv(StringIO("date,machine,planned,operating,total,good,failure\n" + "\n".join(rows) + "\n"))


def test_postprocess_uses_anomaly_detection_when_enough_history_but_no_labeled_failures():
    df = _anomaly_fixture_df()
    config = {"column_map": {
        "date_col": "date", "machine_col": "machine", "planned_time_col": "planned",
        "operating_time_col": "operating", "total_units_col": "total", "good_units_col": "good",
    }, "hourly_value": 1000.0}

    pre = pm.preprocess(df, config)
    assert pre.skip_ml is True
    assert len(pre.context["training_df"]) >= pm._MIN_ANOMALY_ROWS

    out = pm.postprocess(None, config, pre.context)
    machine = out["predictive_result"]["machines"][0]
    assert machine["ml_powered"] is True
    assert 0.0 <= machine["failure_probability"] <= 1.0
    assert machine["failure_probability"] > 0.5  # last 7-day window is the most degraded in the series
    assert any(d["feature"] == "oee_7d" for d in machine["ml_drivers"])
    assert out["pipeline"] is None  # still no trained classifier
    assert out["predictive_result"]["model_meta"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k test_postprocess_uses_anomaly_detection`
Expected: FAIL at `assert machine["ml_powered"] is True` (currently `False` - still using the statistical fallback)

- [ ] **Step 3: Wire the branch into `postprocess()`**

In `backend/app/services/automl_templates/predictive_maintenance.py`, change line 187 from:

```python
    pipeline = engine_result["pipeline"] if engine_result else None
```

to:

```python
    pipeline = engine_result["pipeline"] if engine_result else None
    anomaly_model = _fit_anomaly_model(context.get("training_df"))
```

Then change the per-machine branch (lines 241-254) from:

```python
        if pipeline is not None and machine_key in context["last_x"]:
            x_row = context["last_x"][machine_key]
            feature_cols = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"]
            x_df = pd.DataFrame([dict(zip(feature_cols, x_row))])
            fail_prob = min(float(pipeline.predict_proba(x_df)[0][1]), 0.99)
            ml_powered = True
            drivers: list[dict] = []
        else:
            fail_prob, driver_contribs = legacy._stat_analyze(
                oee_s, avail_s, perf_s, qual_s, last_fail_ago,
                fleet_oee_median=fleet_oee_median,
            )
            ml_powered = False
            drivers = driver_contribs
```

to:

```python
        if pipeline is not None and machine_key in context["last_x"]:
            x_row = context["last_x"][machine_key]
            feature_cols = ["oee_7d", "oee_slope_7d", "scrap_rate_7d", "dt_rate_7d", "fail_count_7d", "days_since_fail"]
            x_df = pd.DataFrame([dict(zip(feature_cols, x_row))])
            fail_prob = min(float(pipeline.predict_proba(x_df)[0][1]), 0.99)
            ml_powered = True
            drivers: list[dict] = []
        elif anomaly_model is not None and machine_key in context["last_x"]:
            model, mean, std = anomaly_model
            fail_prob, driver_contribs = _anomaly_score(model, mean, std, context["last_x"][machine_key])
            ml_powered = True
            drivers = driver_contribs
        else:
            fail_prob, driver_contribs = legacy._stat_analyze(
                oee_s, avail_s, perf_s, qual_s, last_fail_ago,
                fleet_oee_median=fleet_oee_median,
            )
            ml_powered = False
            drivers = driver_contribs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v -k test_postprocess_uses_anomaly_detection`
Expected: 1 passed

- [ ] **Step 5: Run the full template test suite to confirm the tiny-data floor still falls back correctly**

Run: `cd backend && python -m pytest tests/test_predictive_maintenance_template.py -v`
Expected: all passed, including the pre-existing `test_postprocess_without_trained_model_falls_back_to_statistical`, `test_fleet_oee_median_is_shared_across_machines_not_per_machine`, and `test_last_failure_days_ago_uses_failure_col_when_downtime_category_unmapped` (all three use <11-day fixtures, which produce 0 training rows - below `_MIN_ANOMALY_ROWS` - so `anomaly_model` is `None` and they're unaffected)

- [ ] **Step 6: Run the full backend test suite to confirm no cross-module regressions**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/automl_templates/predictive_maintenance.py backend/tests/test_predictive_maintenance_template.py
git commit -m "feat: wire anomaly detection into predictive-maintenance postprocess"
```

---

## Self-Review Notes

- **Spec coverage:** Every section of the design spec maps to a task - constant/floor (Task 1), model + scoring + explanations (Task 2), branch wiring + behavior preservation below the floor (Task 3). The spec's "out of scope" items (Scrap/Downtime templates, wizard UI, real-time scoring) are untouched by this plan, as intended.
- **Type consistency:** `_fit_anomaly_model` returns `tuple[IsolationForest, np.ndarray, np.ndarray] | None` in Task 2 and is unpacked as `model, mean, std = anomaly_model` in Task 3 - consistent. `_anomaly_score` returns `tuple[float, list[dict]]` in Task 2 and is unpacked as `fail_prob, driver_contribs` in Task 3 - consistent with the existing `legacy._stat_analyze()` return shape it sits alongside.
- **No placeholders:** every step has complete, runnable code and exact `pytest -k` commands with expected pass/fail counts.
