from __future__ import annotations

import uuid
from collections import deque

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sqlalchemy import select

from app.db import session as db_session
from app.models.alert import AlertRule
from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.automl_run import AutoMLRun
from app.models.user import User
from app.services import alerts, automl_realtime, automl_storage, realtime_cache

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _seed_cache_rows(stream_id: uuid.UUID, rows: list[dict]) -> realtime_cache.CacheConsumer:
    """Seeds a CacheConsumer's raw-row buffer directly - automl_realtime only
    ever talks to realtime_cache's recent_rows()/set_external_metric()
    interface, so tests exercise that interface without needing a live
    Redpanda broker, same posture as the template/engine tests elsewhere in
    this suite. Installed as the module singleton so both automl_realtime
    and alerts.eval_stream_metric (which reads get_snapshot()) see it."""
    consumer = realtime_cache.CacheConsumer()
    consumer._raw_rows[str(stream_id)] = deque(rows, maxlen=realtime_cache._RAW_ROW_BUFFER)
    realtime_cache._instance = consumer
    return consumer


def _fitted_pipeline_path(tmp_path) -> str:
    X = pd.DataFrame({"x1": [0, 1] * 10, "x2": [1, 0] * 10})
    y = [0, 1] * 10
    pipe = Pipeline([("model", LogisticRegression())])
    pipe.fit(X, y)
    path = tmp_path / "model.joblib"
    joblib.dump(pipe, path)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_cache_after_each_test():
    yield
    automl_realtime.stop_all_binding_schedulers()
    realtime_cache._instance = None


@pytest.mark.asyncio
async def test_score_and_check_binding_publishes_metric_and_fires_stream_alert(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})

    stream_id = uuid.uuid4()
    _seed_cache_rows(stream_id, [{"x1": i % 2, "x2": (i + 1) % 2, "y": i % 2} for i in range(30)])
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            status="success", model_path=model_path,
            meta={"feature_columns": ["x1", "x2"], "task": "classification"},
        )
        db.add(run)
        await db.flush()
        binding = AutoMLLiveBinding(
            org_id=owner.org_id, run_id=run.id, stream_id=stream_id, metric_name="automl_risk_score",
        )
        db.add(binding)
        await db.commit()
        await db.refresh(binding)
        binding_id, org_id = binding.id, owner.org_id

    await automl_realtime.score_and_check_binding(binding_id)

    snap = realtime_cache.get_snapshot(stream_id)
    assert snap is not None
    assert "automl_risk_score" in snap["metrics"]
    assert snap["metrics"]["automl_risk_score"] in (0.0, 1.0)  # LogisticRegression.predict -> a class label here

    async with db_session.async_session_factory() as db:
        refreshed = (await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))).scalar_one()
        assert refreshed.last_scored_at is not None

    # Live-case alerting: automl_realtime published into realtime_cache's
    # external-metrics slot, so a normal stream_metric rule fires with NO new
    # alerts.py code - this is the whole point of the "no new alerting
    # subsystem" design (get_snapshot() merges it into "metrics" for
    # eval_stream_metric, same as a Flink-computed metric would be).
    rule = AlertRule(
        id=uuid.uuid4(), org_id=org_id, created_by=owner.id, name="risk watch", type="stream_metric",
        severity="warning", enabled=True, dataset_id=None,
        config={"stream_id": str(stream_id), "metric_name": "automl_risk_score", "comparison": "gt", "threshold": -1},
    )
    trigger = alerts.eval_stream_metric(rule)
    assert trigger.triggered is True


@pytest.mark.asyncio
async def test_score_and_check_binding_is_noop_when_no_rows_buffered(tmp_path, client):
    """A binding scored before any ticks have arrived in this replica's
    cache (e.g. right after a redeploy) must be a silent no-op, not an
    error - see the module docstring's caveat."""
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner3@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner3@example.com", "password": PASSWORD})

    stream_id = uuid.uuid4()
    _seed_cache_rows(stream_id, [])  # cache instance exists, but empty for this stream
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            status="success", model_path=model_path,
            meta={"feature_columns": ["x1", "x2"], "task": "classification"},
        )
        db.add(run)
        await db.flush()
        binding = AutoMLLiveBinding(
            org_id=owner.org_id, run_id=run.id, stream_id=stream_id, metric_name="automl_risk_score",
        )
        db.add(binding)
        await db.commit()
        await db.refresh(binding)
        binding_id = binding.id

    await automl_realtime.score_and_check_binding(binding_id)

    assert realtime_cache.get_snapshot(stream_id) is None  # nothing published - no rows to score

    async with db_session.async_session_factory() as db:
        refreshed = (await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))).scalar_one()
        assert refreshed.last_scored_at is None


@pytest.mark.asyncio
async def test_drift_detected_with_auto_retrain_produces_new_versioned_run(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner2@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner2@example.com", "password": PASSWORD})

    # Live buffer uses categories completely disjoint from the training
    # baseline below - guarantees a maximal PSI ("high" severity) rather than
    # depending on a borderline threshold.
    stream_id = uuid.uuid4()
    live_rows = [{"x1": "highA" if i % 2 else "highB", "x2": "highB" if i % 2 else "highA", "y": i % 2} for i in range(30)]
    _seed_cache_rows(stream_id, live_rows)
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            feature_columns=["x1", "x2"], status="success", model_path=model_path,
            meta={
                "feature_columns": ["x1", "x2"], "raw_feature_columns": ["x1", "x2"],
                "task": "classification",
            },
        )
        db.add(run)
        await db.flush()
        run_id, org_id = run.id, owner.org_id

        # Training-time baseline: a stable, disjoint category set from the
        # live feed above, persisted the same way automl_runner.py does.
        run_dir = automl_storage.run_dir(org_id, run_id)
        explain_X = pd.DataFrame({"x1": ["lowA", "lowB"] * 10, "x2": ["lowB", "lowA"] * 10})
        explain_X.to_csv(run_dir / "explain_X.csv", index=False)

        binding = AutoMLLiveBinding(
            org_id=org_id, run_id=run_id, stream_id=stream_id, metric_name="automl_risk_score",
            drift_severity_trigger="moderate", auto_retrain_on_drift=True,
        )
        db.add(binding)
        await db.commit()
        await db.refresh(binding)
        binding_id = binding.id

    await automl_realtime.score_and_check_binding(binding_id)

    async with db_session.async_session_factory() as db:
        old_binding = (await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))).scalar_one()
        assert old_binding.last_drift_status == "drifted"
        assert old_binding.enabled is False  # superseded, not reused in place

        new_bindings = (
            await db.execute(
                select(AutoMLLiveBinding).where(
                    AutoMLLiveBinding.org_id == org_id, AutoMLLiveBinding.id != binding_id,
                )
            )
        ).scalars().all()
        assert len(new_bindings) == 1
        new_binding = new_bindings[0]
        assert new_binding.enabled is True
        assert new_binding.run_id != run_id

        new_run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == new_binding.run_id))).scalar_one()
        assert new_run.status == "success", new_run.error
        assert new_run.id != run_id
        assert new_run.source_stream_id == stream_id
        assert new_run.target_column == "y"
