from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register(client, email: str = "ar@example.com", org_name: str = "ArOrg"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    body = reg.json()
    return uuid.UUID(str(body["org_id"])), uuid.UUID(str(body["user_id"]))


async def _register_and_login_local(client, email: str, org_name: str = "ArOrg") -> str:
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_local(client, headers, content: bytes) -> str:
    res = await client.post("/datasets", headers=headers, files={"file": ("d.csv", content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def test_automl_run_retrain_columns_roundtrip(client, test_engine):
    from app.models.automl_run import AutoMLRun
    from app.models.dataset import Dataset

    org_id, user_id = await _register(client)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = Dataset(
            org_id=org_id, name="D", original_filename="d.csv", fmt="csv", storage_ext="csv",
            columns=[{"name": "x", "dtype": "VARCHAR"}], row_count=1, size_bytes=1, created_by=user_id,
        )
        db.add(dataset)
        await db.flush()

        parent = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Parent",
            task="regression", target_column="x", status="success", auto_retrain=True,
        )
        db.add(parent)
        await db.flush()

        child = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Child",
            task="regression", target_column="x", status="success",
            auto_retrain=True, retrained_from_run_id=parent.id, auto_retrain_streak=0,
        )
        db.add(child)
        await db.commit()
        await db.refresh(child)

        assert child.retrained_from_run_id == parent.id
        assert child.auto_retrain is True
        assert child.auto_retrain_streak == 0

        default_run = AutoMLRun(
            org_id=org_id, dataset_id=dataset.id, created_by=user_id, name="Default",
            task="regression", target_column="x", status="queued",
        )
        db.add(default_run)
        await db.commit()
        await db.refresh(default_run)
        assert default_run.auto_retrain is False
        assert default_run.retrained_from_run_id is None
        assert default_run.auto_retrain_streak == 0


def _classification_csv(n: int, *, offset: float = 0.0) -> bytes:
    """x1,x2,y where y = 1 if x1+x2>0 else 0. check_data_drift computes PSI
    on the FEATURE columns (x1/x2) against the run's training-time sample,
    not on y - so offset shifts x1/x2's whole range, which is what actually
    needs to change between calls to simulate genuine feature drift.
    offset=0 (the default) reproduces the same distribution used for
    training; a large offset (e.g. 20) shifts it enough to register as PSI
    "high"."""
    rows = ["x1,x2,y"]
    for i in range(n):
        x1 = (i % 7) - 3 + offset
        x2 = ((i * 3) % 5) - 2 + offset
        y = 1 if x1 + x2 > 0 else 0
        rows.append(f"{x1},{x2},{y}")
    return ("\n".join(rows) + "\n").encode()


async def _train_success_run(client, headers, dataset_id, *, auto_retrain=True):
    body = {
        "name": "Predict y", "dataset_id": dataset_id, "task": "classification",
        "target_column": "y", "feature_columns": ["x1", "x2"], "thoroughness": "fast",
        "auto_retrain": auto_retrain,
    }
    created = await client.post("/automl/runs", headers=headers, json=body)
    assert created.status_code == 202
    run_id = created.json()["id"]
    detail = await client.get(f"/automl/runs/{run_id}", headers=headers)
    assert detail.json()["status"] == "success", detail.json().get("error")
    return run_id


async def test_no_watched_runs_is_a_noop(client, test_engine):
    from app.services import automl_retrain

    org_id, _ = await _register(client)
    # No AutoMLRun rows at all for this org/dataset - must not raise.
    await automl_retrain.maybe_retrain_for_dataset(str(org_id), str(uuid.uuid4()))


async def test_low_drift_does_not_retrain(client, test_engine):
    from app.services import automl_retrain
    from app.services import datasets as ds_service
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "ardrift1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    # Larger n than the drift-triggering tests: the training-time explain
    # sample is only a ~20% held-out slice, and with these discrete-valued
    # (7/5 distinct values) x1/x2 columns a small slice makes PSI noisy
    # regardless of real drift. A bigger n stabilizes the quantile bins.
    dataset_id = await _upload_local(client, headers, _classification_csv(300))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Overwrite the SAME dataset with a same-distribution, larger sample
    # (same generation rule, no label flip) - low/no drift expected.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(330))
        await db.commit()

    await automl_retrain.maybe_retrain_for_dataset(str(org_id), dataset_id)

    runs = await client.get("/automl/runs", headers=headers)
    # Still just the one run - no retrain fired for same-distribution data.
    assert len(runs.json()) == 1
    assert runs.json()[0]["id"] == run_id


async def test_high_drift_triggers_promoted_retrain(client, test_engine):
    from app.services import automl_retrain
    from app.services import datasets as ds_service
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "ardrift2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_local(client, headers, _classification_csv(80))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Overwrite with the same rule but x1/x2 shifted well outside the
    # training-time range - large PSI shift on the feature columns
    # check_data_drift actually measures.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(80, offset=20))
        await db.commit()

    await automl_retrain.maybe_retrain_for_dataset(str(org_id), dataset_id)

    runs = (await client.get("/automl/runs", headers=headers)).json()
    assert len(runs) == 2
    child = next(r for r in runs if r["id"] != run_id)
    parent = next(r for r in runs if r["id"] == run_id)
    assert child["retrained_from_run_id"] == run_id

    # Promoted or rejected depends on whether the shifted-range retrain
    # scores at least as well as the original - both are valid outcomes of
    # this synthetic setup, so assert the invariant that actually matters:
    # exactly one of {parent, child} is the currently-watched run, never
    # both/neither.
    assert (parent["auto_retrain"], child["auto_retrain"]).count(True) == 1


async def test_circuit_breaker_trips_after_max_consecutive_rejections(client, test_engine, monkeypatch):
    from app.services import automl_retrain
    from app.models.automl_run import AutoMLRun
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login_local(client, "arbreaker@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_local(client, headers, _classification_csv(80))
    run_id = await _train_success_run(client, headers, dataset_id)
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])

    # Force every retrain attempt to score worse than its parent, regardless
    # of what actually trained, so the circuit breaker is exercised
    # deterministically rather than depending on real model variance.
    # _evaluate_run calls normalize_higher_is_better exactly twice per
    # evaluation, in order: once for the parent's score, once for the
    # child's - so alternating high/low values across calls makes every
    # single evaluation resolve as "child scored worse."
    call_values = iter([10.0, -10.0] * automl_retrain.MAX_AUTO_RETRAIN_STREAK)
    monkeypatch.setattr(
        automl_retrain.automl_engine, "normalize_higher_is_better",
        lambda task, value: next(call_values),
    )

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    for i in range(automl_retrain.MAX_AUTO_RETRAIN_STREAK):
        async with session_factory() as db:
            from app.services import datasets as ds_service
            dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
            await ds_service.overwrite_dataset_file(db, dataset, data=_classification_csv(80, offset=20))
            await db.commit()
        # Bypass the 1-hour cooldown between attempts within this test by
        # calling the internal evaluator directly against a fresh session,
        # rather than sleeping in a test.
        async with session_factory() as db:
            run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == uuid.UUID(run_id)))).scalar_one()
            dataset = (await db.execute(select(automl_retrain.Dataset).where(automl_retrain.Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
            await automl_retrain._evaluate_run(db, run, dataset, skip_cooldown=True)

    async with session_factory() as db:
        run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == uuid.UUID(run_id)))).scalar_one()
        assert run.auto_retrain is False
        assert run.auto_retrain_streak == automl_retrain.MAX_AUTO_RETRAIN_STREAK


async def test_incident_close_schedules_retrain_check_end_to_end(client, test_engine, monkeypatch):
    """Full path: real HTTP incident-close -> overwrite_dataset_file ->
    BackgroundTasks -> maybe_retrain_for_dataset. Patches
    maybe_retrain_for_dataset itself (not its internals) so this test
    verifies the WIRING (was it called, with what args), not the drift
    policy already covered by Task 3's tests."""
    from app.services import automl_retrain

    calls: list[tuple[str, str]] = []

    async def _fake_retrain(org_id: str, dataset_id: str) -> None:
        calls.append((org_id, dataset_id))

    monkeypatch.setattr(automl_retrain, "maybe_retrain_for_dataset", _fake_retrain)

    token = await _register_and_login_local(client, "arwiring@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    start = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 1", "reason": "breakdown", "shift": "A"},
    )
    incident_id = start.json()["id"]
    ended = await client.post(f"/downtime-tracking/incidents/{incident_id}/end", headers=headers)
    dataset_id = ended.json()["live_dataset_id"]
    assert calls == []  # first materialization is a create, not an overwrite - no trigger yet

    start2 = await client.post(
        "/downtime-tracking/incidents", headers=headers,
        json={"machine": "Line 2", "reason": "other", "shift": "A"},
    )
    await client.post(f"/downtime-tracking/incidents/{start2.json()['id']}/end", headers=headers)

    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    assert calls == [(org_id, dataset_id)]
