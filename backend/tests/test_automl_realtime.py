from __future__ import annotations

import uuid

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
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream, RealtimeStreamMetric
from app.models.user import User
from app.services import alerts, automl_realtime, automl_storage

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _seed_stream_with_rows(db, rows: list[dict]) -> uuid.UUID:
    """Creates an Integration + (stopped) RealtimeStream + one persisted
    sample carrying `rows` - automl_realtime only ever talks to
    app.services.realtime's recent_rows()/set_external_metric() interface,
    so tests exercise that interface through the real tables."""
    owner = (await db.execute(select(User))).scalars().first()
    integration = Integration(org_id=owner.org_id, name="src", type="erp_rest", config={}, created_by=owner.id)
    db.add(integration)
    await db.flush()
    stream = RealtimeStream(
        org_id=owner.org_id, integration_id=integration.id, name="Live", column_map={},
        sensor_map={}, interval_seconds=5, accumulate=True, desired_status="stopped",
        created_by=owner.id,
    )
    db.add(stream)
    await db.flush()
    if rows:
        db.add(RealtimeSample(
            org_id=owner.org_id, stream_id=stream.id, status="running",
            rows_in_window=len(rows), rows_collected=len(rows), duration_s=1,
            metrics={}, raw_rows=rows,
        ))
    await db.commit()
    return stream.id


def _fitted_pipeline_path(tmp_path) -> str:
    X = pd.DataFrame({"x1": [0, 1] * 10, "x2": [1, 0] * 10})
    y = [0, 1] * 10
    pipe = Pipeline([("model", LogisticRegression())])
    pipe.fit(X, y)
    path = tmp_path / "model.joblib"
    joblib.dump(pipe, path)
    return str(path)


@pytest.fixture(autouse=True)
def _stop_schedulers_after_each_test():
    yield
    automl_realtime.stop_all_binding_schedulers()


@pytest.mark.asyncio
async def test_score_and_check_binding_publishes_metric_and_fires_stream_alert(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})

    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        stream_id = await _seed_stream_with_rows(db, [{"x1": i % 2, "x2": (i + 1) % 2, "y": i % 2} for i in range(30)])
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

    async with db_session.async_session_factory() as db:
        metric = (
            await db.execute(
                select(RealtimeStreamMetric).where(
                    RealtimeStreamMetric.stream_id == stream_id,
                    RealtimeStreamMetric.name == "automl_risk_score",
                )
            )
        ).scalar_one()
        assert metric.value in (0.0, 1.0)  # LogisticRegression.predict -> a class label here

    async with db_session.async_session_factory() as db:
        refreshed = (await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))).scalar_one()
        assert refreshed.last_scored_at is not None

    # Live-case alerting: automl_realtime published into the stream's
    # realtime_stream_metrics row, so a normal stream_metric rule fires with
    # NO new alerts.py code - this is the whole point of the "no new alerting
    # subsystem" design (latest_snapshot() merges external metrics into
    # "metrics" for eval_stream_metric, same as a collector-computed one).
    rule = AlertRule(
        id=uuid.uuid4(), org_id=org_id, created_by=owner.id, name="risk watch", type="stream_metric",
        severity="warning", enabled=True, dataset_id=None,
        config={"stream_id": str(stream_id), "metric_name": "automl_risk_score", "comparison": "gt", "threshold": -1},
    )
    async with db_session.async_session_factory() as db:
        trigger = await alerts.eval_stream_metric(db, org_id, rule)
    assert trigger.triggered is True


@pytest.mark.asyncio
async def test_score_and_check_binding_is_noop_when_no_rows_buffered(tmp_path, client):
    """A binding scored before its stream has persisted any sample (e.g. a
    stream created but never started) must be a silent no-op, not an
    error - see the module docstring."""
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner3@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner3@example.com", "password": PASSWORD})

    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        stream_id = await _seed_stream_with_rows(db, [])  # stream row exists, but no samples yet
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

    async with db_session.async_session_factory() as db:
        metric_rows = (
            await db.execute(select(RealtimeStreamMetric).where(RealtimeStreamMetric.stream_id == stream_id))
        ).scalars().all()
    assert metric_rows == []  # nothing published - no rows to score

    async with db_session.async_session_factory() as db:
        refreshed = (await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))).scalar_one()
        assert refreshed.last_scored_at is None


@pytest.mark.asyncio
async def test_drift_detected_with_auto_retrain_produces_new_versioned_run(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner2@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner2@example.com", "password": PASSWORD})

    # The persisted live sample uses categories completely disjoint from the
    # training baseline below - guarantees a maximal PSI ("high" severity)
    # rather than depending on a borderline threshold.
    live_rows = [{"x1": "highA" if i % 2 else "highB", "x2": "highB" if i % 2 else "highA", "y": i % 2} for i in range(30)]
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        stream_id = await _seed_stream_with_rows(db, live_rows)
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
