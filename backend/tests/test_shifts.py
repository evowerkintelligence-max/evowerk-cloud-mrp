from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

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
