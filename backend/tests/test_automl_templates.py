from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pandas as pd
import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.models.user import User
from app.services import automl_templates as templates
from app.services.automl_templates import predictive_maintenance as pm
from app.services.automl_runner import execute_automl_run

PASSWORD = TEST_PASSWORD


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
    assert len(training_df) == 0  # no full W+H window fits, so zero training rows
    # n=7 < W+H+1=11, so the machine is skipped entirely: no training rows AND
    # no last-window snapshot. This matches predictive.py's original
    # _ml_analyze, which guards both with a single `if n < W + H + 1: continue`
    # so a machine never gets ML-scored on partial history alone.
    assert "M1" not in last_by_machine


def test_has_ml_signal_requires_min_rows_and_balanced_positive_rate():
    enough_balanced = pd.DataFrame({"is_failure_next": [0, 1] * 25})  # 50 rows, 50% positive
    assert pm.has_ml_signal(enough_balanced) is True

    too_few = pd.DataFrame({"is_failure_next": [0, 1] * 5})  # 10 rows
    assert pm.has_ml_signal(too_few) is False

    all_positive = pd.DataFrame({"is_failure_next": [1] * 50})  # pos_rate=1.0 > 0.90
    assert pm.has_ml_signal(all_positive) is False


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

    reg = await client.post("/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
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
