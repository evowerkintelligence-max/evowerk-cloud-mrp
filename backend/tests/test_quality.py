from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# batch B has a clearly higher defect rate and a distinct temp range, so both
# the worst-batch pick and the temp<->defect correlation are unambiguous.
QUALITY_CSV = (
    b"batch,defect,temp\n"
    b"A,0,70\nA,0,71\nA,0,69\nA,0,70\nA,1,72\nA,0,70\n"
    b"B,1,95\nB,1,96\nB,0,94\nB,1,97\nB,1,95\nB,1,98\n"
)


async def _register_and_login(client):
    reg = await client.post(
        "/auth/register", json={"org_name": "Acme", "email": "owner@example.com", "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    return login.json()["access_token"]


async def _upload_dataset(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("quality.csv", QUALITY_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_quality_analyze_flags_worst_batch_and_correlation(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "batch_column": "batch", "feature_columns": ["temp"]},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["summary"]["total_records"] == 12
    assert body["summary"]["total_defects"] == 6
    assert body["summary"]["metric_type"] == "rate"
    assert body["summary"]["worst_batch"] == "B"
    assert len(body["batch_stats"]) == 2

    corr = next(c for c in body["correlations"] if c["feature"] == "temp")
    assert corr["direction"] == "positive"
    assert corr["correlation"] > 0.5


async def test_quality_analyze_rejects_unknown_defect_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "nope"},
    )
    assert res.status_code == 400
