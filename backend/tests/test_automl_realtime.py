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
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick
from app.models.user import User
from app.services import alerts, automl_realtime, automl_storage

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


def _fitted_pipeline_path(tmp_path) -> str:
    X = pd.DataFrame({"x1": [0, 1] * 10, "x2": [1, 0] * 10})
    y = [0, 1] * 10
    pipe = Pipeline([("model", LogisticRegression())])
    pipe.fit(X, y)
    path = tmp_path / "model.joblib"
    joblib.dump(pipe, path)
    return str(path)


async def _make_schedule(db, org_id: uuid.UUID, created_by: uuid.UUID) -> SyncSchedule:
    integration = Integration(org_id=org_id, name="Cloud DB", type="postgres", config={}, created_by=created_by)
    db.add(integration)
    await db.flush()
    schedule = SyncSchedule(
        org_id=org_id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=created_by,
    )
    db.add(schedule)
    await db.flush()
    return schedule


async def _seed_sync_rows(db, schedule_id: uuid.UUID, rows: list[dict]) -> None:
    for row in rows:
        db.add(SyncRow(schedule_id=schedule_id, row=row))
    await db.flush()


@pytest.mark.asyncio
async def test_score_and_check_binding_merges_score_into_tick_metrics(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        schedule = await _make_schedule(db, owner.org_id, owner.id)
        await _seed_sync_rows(db, schedule.id, [{"x1": i % 2, "x2": (i + 1) % 2, "y": i % 2} for i in range(30)])

        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            status="success", model_path=model_path,
            meta={"feature_columns": ["x1", "x2"], "task": "classification"},
        )
        db.add(run)
        await db.flush()
        binding = AutoMLLiveBinding(
            org_id=owner.org_id, run_id=run.id, schedule_id=schedule.id, metric_name="automl_risk_score",
        )
        db.add(binding)
        tick = SyncTick(schedule_id=schedule.id, org_id=owner.org_id, row_count=1, metrics={"throughput": 1.0})
        db.add(tick)
        await db.commit()
        await db.refresh(binding)
        await db.refresh(tick)
        binding_id, org_id, schedule_id, tick_id = binding.id, owner.org_id, schedule.id, tick.id

    async with db_session.async_session_factory() as db:
        tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        await automl_realtime.score_and_check_binding(db, binding_id, tick)
        await db.commit()

    async with db_session.async_session_factory() as db:
        refreshed_tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        assert refreshed_tick.metrics["throughput"] == 1.0  # existing metrics preserved
        assert refreshed_tick.metrics["automl_risk_score"] in (0.0, 1.0)  # LogisticRegression.predict -> a class label here

        refreshed_binding = (
            await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))
        ).scalar_one()
        assert refreshed_binding.last_scored_at is not None

    # Live-case alerting: automl_realtime merged its score directly into the
    # SyncTick.metrics dict, so a normal stream_metric rule fires with NO new
    # alerts.py code - this is the whole point of the "no new alerting
    # subsystem" design (eval_stream_metric already reads a schedule's
    # latest tick, same as an ordinary OEE metric would be).
    async with db_session.async_session_factory() as db:
        rule = AlertRule(
            id=uuid.uuid4(), org_id=org_id, created_by=owner.id, name="risk watch", type="stream_metric",
            severity="warning", enabled=True, dataset_id=None,
            config={"schedule_id": str(schedule_id), "metric_name": "automl_risk_score", "comparison": "gt", "threshold": -1},
        )
        trigger = await alerts.eval_stream_metric(db, rule)
    assert trigger.triggered is True


@pytest.mark.asyncio
async def test_score_and_check_binding_is_noop_when_no_rows_synced(tmp_path, client):
    """A binding scored before any rows have synced for this schedule must
    be a silent no-op, not an error."""
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner3@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner3@example.com", "password": PASSWORD})
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        schedule = await _make_schedule(db, owner.org_id, owner.id)
        # deliberately no SyncRow rows added

        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            status="success", model_path=model_path,
            meta={"feature_columns": ["x1", "x2"], "task": "classification"},
        )
        db.add(run)
        await db.flush()
        binding = AutoMLLiveBinding(
            org_id=owner.org_id, run_id=run.id, schedule_id=schedule.id, metric_name="automl_risk_score",
        )
        db.add(binding)
        tick = SyncTick(schedule_id=schedule.id, org_id=owner.org_id, row_count=0, metrics={})
        db.add(tick)
        await db.commit()
        await db.refresh(binding)
        await db.refresh(tick)
        binding_id, tick_id = binding.id, tick.id

    async with db_session.async_session_factory() as db:
        tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        await automl_realtime.score_and_check_binding(db, binding_id, tick)
        await db.commit()

    async with db_session.async_session_factory() as db:
        refreshed_tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        assert refreshed_tick.metrics == {}  # nothing published - no rows to score

        refreshed_binding = (
            await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))
        ).scalar_one()
        assert refreshed_binding.last_scored_at is None


@pytest.mark.asyncio
async def test_score_and_check_binding_disabled_binding_is_noop(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner4@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner4@example.com", "password": PASSWORD})
    model_path = _fitted_pipeline_path(tmp_path)

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        schedule = await _make_schedule(db, owner.org_id, owner.id)
        await _seed_sync_rows(db, schedule.id, [{"x1": 0, "x2": 1, "y": 0}])

        run = AutoMLRun(
            org_id=owner.org_id, dataset_id=uuid.uuid4(), created_by=owner.id,
            name="live risk model", task="classification", target_column="y",
            status="success", model_path=model_path,
            meta={"feature_columns": ["x1", "x2"], "task": "classification"},
        )
        db.add(run)
        await db.flush()
        binding = AutoMLLiveBinding(
            org_id=owner.org_id, run_id=run.id, schedule_id=schedule.id, metric_name="automl_risk_score",
            enabled=False,
        )
        db.add(binding)
        tick = SyncTick(schedule_id=schedule.id, org_id=owner.org_id, row_count=1, metrics={})
        db.add(tick)
        await db.commit()
        await db.refresh(binding)
        await db.refresh(tick)
        binding_id, tick_id = binding.id, tick.id

    async with db_session.async_session_factory() as db:
        tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        await automl_realtime.score_and_check_binding(db, binding_id, tick)  # must not raise
        await db.commit()

    async with db_session.async_session_factory() as db:
        refreshed_tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        assert refreshed_tick.metrics == {}


@pytest.mark.asyncio
async def test_drift_detected_with_auto_retrain_produces_new_versioned_run(tmp_path, client):
    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner2@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    await client.post("/auth/login", json={"email": "owner2@example.com", "password": PASSWORD})
    model_path = _fitted_pipeline_path(tmp_path)

    # Live buffer uses categories completely disjoint from the training
    # baseline below - guarantees a maximal PSI ("high" severity) rather than
    # depending on a borderline threshold.
    live_rows = [{"x1": "highA" if i % 2 else "highB", "x2": "highB" if i % 2 else "highA", "y": i % 2} for i in range(30)]

    async with db_session.async_session_factory() as db:
        owner = (await db.execute(select(User))).scalars().first()
        schedule = await _make_schedule(db, owner.org_id, owner.id)
        await _seed_sync_rows(db, schedule.id, live_rows)

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
            org_id=org_id, run_id=run_id, schedule_id=schedule.id, metric_name="automl_risk_score",
            drift_severity_trigger="moderate", auto_retrain_on_drift=True,
        )
        db.add(binding)
        tick = SyncTick(schedule_id=schedule.id, org_id=org_id, row_count=len(live_rows), metrics={})
        db.add(tick)
        await db.commit()
        await db.refresh(binding)
        await db.refresh(tick)
        binding_id, tick_id = binding.id, tick.id

    async with db_session.async_session_factory() as db:
        tick = (await db.execute(select(SyncTick).where(SyncTick.id == tick_id))).scalar_one()
        await automl_realtime.score_and_check_binding(db, binding_id, tick)
        # score_and_check_binding's drift+retrain path commits internally
        # (see _retrain_on_drift's docstring) - no extra commit needed here.

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
        assert new_binding.schedule_id == schedule.id

        new_run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == new_binding.run_id))).scalar_one()
        assert new_run.status == "success", new_run.error
        assert new_run.id != run_id
        assert new_run.source_stream_id == schedule.id
        assert new_run.target_column == "y"
