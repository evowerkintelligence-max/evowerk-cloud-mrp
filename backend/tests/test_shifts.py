from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

# Shift A: 90% avail x 95% qual = 85.5 OEE. Shift B: 80% x 90% = 72.0. A best, B worst.
SHIFT_CSV = b"shift,planned,operating,total,good\nA,100,90,100,95\nB,100,80,100,90\n"


async def _token(client):
    await client.post("/auth/register", json={"org_name": "Acme", "email": "o@x.com", "password": PASSWORD})
    r = await client.post("/auth/login", json={"email": "o@x.com", "password": PASSWORD})
    return r.json()["access_token"]


async def test_shifts_best_worst_by_oee(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}
    up = await client.post("/datasets", files={"file": ("s.csv", SHIFT_CSV, "text/csv")}, headers=h)
    ds = up.json()["id"]

    r = await client.post(
        "/analytics/shifts",
        json={
            "dataset_id": ds,
            "shift_col": "shift",
            "planned_col": "planned",
            "operating_col": "operating",
            "total_units_col": "total",
            "good_units_col": "good",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert "oee" in b["metrics_available"]
    assert b["best_shift"] == "A"
    assert b["worst_shift"] == "B"
    oee = {s["shift"]: s["oee_pct"] for s in b["shifts"]}
    assert oee["A"] == 85.5
    assert oee["B"] == 72.0


async def test_shifts_no_metric_columns_400(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}
    up = await client.post("/datasets", files={"file": ("s.csv", SHIFT_CSV, "text/csv")}, headers=h)
    ds = up.json()["id"]
    r = await client.post("/analytics/shifts", json={"dataset_id": ds, "shift_col": "shift"}, headers=h)
    assert r.status_code == 400


MACHINE_CSV = b"shift,machine,units\nA,M1,10\nA,M2,20\nB,M1,5\n"


async def test_shifts_by_machine_breakdown(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}
    up = await client.post("/datasets", files={"file": ("m.csv", MACHINE_CSV, "text/csv")}, headers=h)
    ds = up.json()["id"]

    r = await client.post(
        "/analytics/shifts",
        json={"dataset_id": ds, "shift_col": "shift", "units_col": "units", "machine_col": "machine"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    by_machine = {(m["shift"], m["machine"]): m["total_units"] for m in r.json()["by_machine"]}
    assert by_machine == {("A", "M1"): 10.0, ("A", "M2"): 20.0, ("B", "M1"): 5.0}


TREND_CSV = b"shift,units,ts\nA,10,2026-07-01\nA,20,2026-07-02\nB,5,2026-07-01\n"


async def test_shifts_trend_across_days(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}
    up = await client.post("/datasets", files={"file": ("t.csv", TREND_CSV, "text/csv")}, headers=h)
    ds = up.json()["id"]

    r = await client.post(
        "/analytics/shifts",
        json={"dataset_id": ds, "shift_col": "shift", "units_col": "units", "timestamp_col": "ts"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    trend = {(t["period"], t["shift"]): t["value"] for t in r.json()["trend"]}
    assert trend == {
        ("2026-07-01", "A"): 10.0,
        ("2026-07-02", "A"): 20.0,
        ("2026-07-01", "B"): 5.0,
    }


GARBLED_CSV = b"shift,units\nA,10\nA,abc\nB,5\n"


async def test_shifts_garbled_cell_flags_data_quality(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}
    up = await client.post("/datasets", files={"file": ("g.csv", GARBLED_CSV, "text/csv")}, headers=h)
    ds = up.json()["id"]

    r = await client.post(
        "/analytics/shifts",
        json={"dataset_id": ds, "shift_col": "shift", "units_col": "units"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["invalid_cells"] > 0
    assert b["rows_with_issues"] > 0


async def test_shift_schedule_round_trip(client):
    token = await _token(client)
    h = {"Authorization": f"Bearer {token}"}

    put = await client.put(
        "/analytics/shifts/schedule",
        json=[
            {"shift_label": "A", "start_time": "06:00", "end_time": "14:00"},
            {"shift_label": "B", "start_time": "14:00", "end_time": "22:00"},
        ],
        headers=h,
    )
    assert put.status_code == 200, put.text

    get = await client.get("/analytics/shifts/schedule", headers=h)
    assert get.status_code == 200, get.text
    by_label = {s["shift_label"]: (s["start_time"], s["end_time"]) for s in get.json()}
    assert by_label == {"A": ("06:00", "14:00"), "B": ("14:00", "22:00")}
