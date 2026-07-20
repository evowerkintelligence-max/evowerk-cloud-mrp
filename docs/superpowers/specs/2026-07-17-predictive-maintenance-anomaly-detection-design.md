# Predictive Maintenance: Anomaly Detection for Low-Data Machines - Design

**Goal:** Replace `predictive.py`'s hand-tuned statistical heuristic (`_stat_analyze`) with real unsupervised anomaly detection for machines that have some history but not enough labeled failures to train the AutoML classifier - closing the gap called out in review: "Predictive Maintenance still falls back to the old statistical heuristic on low-data machines, not real anomaly detection - this matters because real failure data is almost always 99% normal."

**Scope:** `backend/app/services/automl_templates/predictive_maintenance.py` only. Scrap Prediction and Downtime Risk templates (`scrap_prediction.py`, `downtime_risk.py`) have no statistical-fallback concept - they always run through `automl_engine` - so they're untouched by this change.

## Current state

`postprocess()` has one fallback branch today:

```python
if pipeline is not None and machine_key in context["last_x"]:
    # classifier scores this machine
    ...
else:
    # legacy._stat_analyze(): 8 hand-tuned heuristic signals, fixed weights
    ...
```

The `else` branch fires whenever `preprocess()` decided there wasn't enough labeled signal to train a classifier at all (`has_ml_signal()` requires >= 40 rolling windows with a 5-90% positive rate), or when a specific machine lacks a `last_x` snapshot. Because real failure data is rare, this is the common path for most machines in practice - and it's a fixed heuristic, not a model.

## New design

Insert a middle branch between the classifier and the heuristic:

```
classifier (has enough labeled failures)
  -> anomaly detection (has some history, not enough labeled failures)
  -> statistical heuristic (has almost no history at all)
```

### The model

`sklearn.ensemble.IsolationForest` - already a dependency (`scikit-learn>=1.4.0` in `backend/requirements.txt`), no new package. It is fit **once per `postprocess()` call**, on the same rolling-window feature columns `build_training_frame()` already produces (`legacy._ML_FEATURES`: `oee_7d`, `oee_slope_7d`, `scrap_rate_7d`, `dt_rate_7d`, `fail_count_7d`, `days_since_fail`), across all machines' windows pooled together. The `is_failure_next` label is ignored entirely - IsolationForest is unsupervised, which is exactly what makes it usable when there aren't enough positive labels for the classifier.

Each machine is then scored against that fitted baseline using its latest window (`context["last_x"][machine_key]`, already computed by `build_training_frame()` for every machine with >= `_W` (7) days of history, independent of whether it contributed classifier training rows).

### The floor

A new constant `_MIN_ANOMALY_ROWS = 15`. `preprocess()`'s existing `training_df` (the full rolling-window feature table, before the `has_ml_signal()` gate) is added to `context["training_df"]` so `postprocess()` can access it regardless of whether the classifier ran.

- `len(training_df) >= _MIN_ANOMALY_ROWS` -> fit IsolationForest, use it for any machine not covered by the classifier.
- `len(training_df) < _MIN_ANOMALY_ROWS` -> too little data to establish a "normal" baseline; `legacy._stat_analyze()` remains the last resort, unchanged from today.

This threshold is deliberately well below the classifier's `_MIN_ROWS = 40` (which also requires a balanced-ish positive rate) - anomaly detection only needs enough rows to characterize "normal," not enough labeled positives to characterize "failure."

### Score -> probability

`IsolationForest.decision_function(x)` returns a positive value for inliers (normal) and negative for outliers (anomalous), unbounded. This is squashed into the same 0-1 `failure_probability` scale everything else in this template already uses, via a fixed logistic transform:

```python
prob = 1.0 / (1.0 + math.exp(5.0 * decision))
```

`decision = 0` maps to `prob = 0.5`; increasingly negative (anomalous) decisions push `prob` toward 1. This keeps the existing risk-level thresholds (`>= 0.65` high, `>= 0.35` medium, in `_machine_risk_from_prob()`) working unchanged - they were already designed around a 0-1 probability-like score, not specifically a classifier's `predict_proba()`.

### Explanations (`ml_drivers` / `top_driver`)

To keep the existing per-machine driver breakdown populated (rendered in the UI, and consumed by `_ACTIONS` lookup for recommendation text), each feature's z-score against the training baseline's mean/std is converted into a directional "badness" score, matching the same semantics `_stat_analyze` already encodes for its own signals:

| Feature | Bad direction |
|---|---|
| `oee_7d` | low |
| `oee_slope_7d` | falling (negative) |
| `scrap_rate_7d` | high |
| `dt_rate_7d` | high |
| `fail_count_7d` | high |
| `days_since_fail` | low (recent failure) |

The top 3 by badness are returned in the same `{feature, label, importance, elevated}` shape `_stat_analyze`'s contributions already use, reusing `legacy._ML_FEAT_LABELS` for the `label` field - no new vocabulary for the frontend to learn.

### What doesn't change

- `PredictiveResult`, `MachineRisk`, `ModelMeta` dataclasses/JSON shapes - untouched.
- `model_meta` stays `None` when no classifier trained (same as today) - an "accuracy %" has no honest meaning for an unsupervised model, so no fake metric is invented for it.
- `ml_powered` becomes `True` for anomaly-scored machines (it genuinely is model-scored now) - the only field the frontend reads from this branch today (`Predictive.tsx` uses `model_meta`'s presence, not `ml_powered`, to decide whether to show the algorithm badge), so this is a safe, additive change.
- No frontend changes, no API changes, no migration.

## Testing

Extends `backend/tests/test_predictive_maintenance_template.py` following its existing fixture pattern (`_fixture_df()`-style CSV construction):

1. A fixture with enough rolling-window rows to clear `_MIN_ANOMALY_ROWS` but not `_MIN_ROWS`/`has_ml_signal()`'s positive-rate requirement (e.g. all-healthy machines with zero or very few failures) - asserts `preprocess()` still returns `skip_ml=True`, and `postprocess()` produces `ml_powered=True` machines with a `failure_probability` in `[0, 1]` and a non-empty `ml_drivers` list.
2. A fixture below `_MIN_ANOMALY_ROWS` (the existing tiny-data fixture already used for the `_stat_analyze` fallback test) - asserts the heuristic path still fires (`ml_powered=False`), unchanged from the current behavior.
3. A unit test on the z-score -> badness/driver conversion directly (not just through the full `postprocess()` pipeline), covering at least one "high is bad" and one "low is bad" feature.

## Out of scope (unchanged from the broader AutoML review)

- Scrap Prediction / Downtime Risk templates - no equivalent fallback exists there.
- Guided wizard UI, plain-English explanation summaries, real-time scoring, live alerting - separate sub-projects, tracked independently.
