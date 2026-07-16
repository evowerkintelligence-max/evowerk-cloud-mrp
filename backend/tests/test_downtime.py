from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

# 3 real incidents (duration > 0): 30 + 20 + 50 = 100 min.
#   Breakdown = 80 min (2 incidents), Changeover = 20 min (1). MTTR = 100/3 = 33.33.
#   The "0" row and the blank-duration row are not incidents.
DT_CSV = (
    b"duration,category,machine,ts\n"
    b"30,Breakdown,M1,2026-01-01 08:00\n"
    b"20,Changeover,M1,2026-01-01 10:00\n"
    b"50,Breakdown,M2,2026-01-02 09:00\n"
    b"0,Breakdown,M2,2026-01-02 11:00\n"
    b",Idle,M1,2026-01-03\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=DT_CSV):
    res = await client.post(
        "/datasets", files={"file": ("dt.csv", data, "text/csv")}, headers=_h(token)
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_downtime_pareto_and_mttr(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/downtime",
        json={
            "dataset_id": dataset_id,
            "duration_col": "duration",
            "category_col": "category",
            "machine_col": "machine",
            "timestamp_col": "ts",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_incidents"] == 3
    assert body["total_minutes"] == 100.0
    assert body["mttr_minutes"] == 33.33
    assert body["top_category"] == "Breakdown"

    cats = {c["category"]: c for c in body["by_category"]}
    assert cats["Breakdown"]["total_minutes"] == 80.0
    assert cats["Breakdown"]["incidents"] == 2
    assert cats["Breakdown"]["pct_of_total"] == 80.0
    assert cats["Changeover"]["cumulative_pct"] == 100.0

    assert {m["machine"] for m in body["by_machine"]} == {"M1", "M2"}
    assert len(body["trend"]) == 2  # two distinct days with incidents


async def test_downtime_hours_unit(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/downtime",
        json={"dataset_id": dataset_id, "duration_col": "duration", "duration_unit": "hours"},
        headers=_h(token),
    )
    assert res.status_code == 200
    # Same durations read as hours → ×60 minutes.
    assert res.json()["total_minutes"] == 6000.0


async def test_downtime_negative_duration_floored_and_flagged(client):
    token = await _register_and_login(client)
    csv = (
        b"duration,category,machine,ts\n"
        b"30,Breakdown,M1,2026-01-01 08:00\n"
        b"-20,Breakdown,M2,2026-01-02 09:00\n"
    )
    dataset_id = await _upload(client, token, data=csv)

    res = await client.post(
        "/analytics/downtime",
        json={
            "dataset_id": dataset_id,
            "duration_col": "duration",
            "category_col": "category",
            "machine_col": "machine",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # negative duration is floored to 0, so it's not a real (> 0) incident.
    assert body["total_incidents"] == 1
    assert body["total_minutes"] == 30.0
    assert body["invalid_cells"] == 1
    assert body["rows_with_issues"] == 1

    # M2's only row is the negative one: 0 incidents, but it must still surface
    # as an invalid cell instead of silently vanishing from the breakdown.
    machines = {m["machine"]: m for m in body["by_machine"]}
    assert machines["M1"] == {
        "machine": "M1", "incidents": 1, "total_minutes": 30.0, "mttr_minutes": 30.0, "invalid_cells": 0,
    }
    assert machines["M2"]["incidents"] == 0
    assert machines["M2"]["total_minutes"] == 0.0
    assert machines["M2"]["invalid_cells"] == 1

    cats = {c["category"]: c for c in body["by_category"]}
    assert cats["Breakdown"]["invalid_cells"] == 1


async def test_downtime_unknown_column_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    res = await client.post(
        "/analytics/downtime",
        json={"dataset_id": dataset_id, "duration_col": "nope"},
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_downtime_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)
    res = await client.post(
        "/analytics/downtime",
        json={"dataset_id": dataset_id, "duration_col": "duration"},
        headers=_h(token_b),
    )
    assert res.status_code == 404


async def test_downtime_dataset_not_found(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/analytics/downtime",
        json={"dataset_id": str(uuid.uuid4()), "duration_col": "duration"},
        headers=_h(token),
    )
    assert res.status_code == 404
