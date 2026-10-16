from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

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


async def test_quality_dpmo_and_cost_of_quality(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "opportunities_per_unit": 2, "cost_per_defect": 15},
    )
    assert res.status_code == 200
    summary = res.json()["summary"]

    # 6 defects / (12 records * 2 opportunities) * 1e6
    assert summary["dpmo"] == 250_000.0
    assert summary["sigma_level"] is not None
    assert summary["total_cost_of_quality"] == 90.0  # 6 defects * $15


async def test_quality_dpmo_and_cost_of_quality_default_off(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect"},
    )
    assert res.status_code == 200
    summary = res.json()["summary"]

    assert summary["dpmo"] is not None  # opportunities_per_unit defaults to 1
    assert summary["cost_per_defect"] is None
    assert summary["total_cost_of_quality"] is None


async def test_quality_counts_column_uses_dpu_metric(client):
    # defect column holds counts (values > 1) → DPU, not a bogus percentage.
    counts_csv = (
        b"batch,defect,temp\n"
        b"A,0,70\nA,2,71\nA,1,69\nA,0,70\nA,3,72\nA,1,70\n"
        b"B,5,95\nB,4,96\nB,6,94\nB,5,97\nB,7,95\nB,4,98\n"
    )
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("counts.csv", counts_csv, "text/csv")}
    )
    dataset_id = res.json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "batch_column": "batch"},
    )
    assert res.status_code == 200
    s = res.json()["summary"]
    assert s["metric_type"] == "dpu"
    assert s["defect_rate_pct"] is None
    assert s["defect_metric"] == round(38 / 12, 3)
    assert s["metric_suffix"] == ""


async def test_quality_truncates_at_max_rows(client):
    csv = b"batch,defect\n" + b"".join(b"A,%d\n" % (i % 2) for i in range(150))
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": ("big.csv", csv, "text/csv")})
    dataset_id = res.json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "max_rows": 100},
    )
    assert res.status_code == 200
    s = res.json()["summary"]
    assert s["truncated"] is True
    assert s["rows_analyzed"] == 100
    assert s["rows_total"] == 150


async def test_quality_cluster_fallback_without_sklearn(client, monkeypatch):
    from app.services import quality_engine

    monkeypatch.setattr(quality_engine, "_HAS_SKLEARN", False)
    counts_csv = (
        b"batch,defect,temp\n"
        b"A,0,70\nA,2,71\nA,1,69\nA,0,70\nA,3,72\nA,1,70\n"
        b"B,5,95\nB,4,96\nB,6,94\nB,5,97\nB,7,95\nB,4,98\n"
    )
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("counts.csv", counts_csv, "text/csv")}
    )
    dataset_id = res.json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "feature_columns": ["temp"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["sklearn_available"] is False
    assert len(body["clusters"]) > 0
    assert all(c["feature_means"] == {} for c in body["clusters"])


async def test_quality_negative_defect_values_warn(client):
    csv = b"batch,defect\nA,-1\nA,1\nA,0\nA,2\nA,1\nA,0\n"
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": ("neg.csv", csv, "text/csv")})
    dataset_id = res.json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect"},
    )
    assert res.status_code == 200
    assert any("negative" in w.lower() for w in res.json()["warnings"])


async def test_quality_trend_gets_control_limits_and_flags_spike(client):
    # A flat 25% defect rate for 14 days, with one day (01-08) spiking to
    # 100% - that single jump (and the matching drop back down) should trip
    # the moving-range control limits while the other 14 stay in control.
    lines = ["date,defect"]
    for day in range(1, 16):
        date = f"2026-01-{day:02d}"
        defects = [1, 1, 1, 1] if day == 8 else [1, 0, 0, 0]
        for d in defects:
            lines.append(f"{date},{d}")
    csv = ("\n".join(lines) + "\n").encode()

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": ("trend.csv", csv, "text/csv")})
    dataset_id = res.json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/quality/analyze",
        headers=headers,
        json={"defect_column": "defect", "timestamp_column": "date"},
    )
    assert res.status_code == 200
    body = res.json()

    assert len(body["trend"]) == 15
    tc = body["trend_control"]
    assert tc is not None
    assert tc["chart_type"] == "p"  # binary defect column -> proportion chart
    assert tc["lcl"] <= tc["centerline"] <= tc["ucl"]
    assert tc["out_of_control_count"] >= 1
    assert tc["low_sample_warning"] is True  # 15 periods, under the 20-period baseline minimum

    spike = next(t for t in body["trend"] if t["date"] == "2026-01-08")
    assert spike["in_control"] is False
    assert 1 in spike["violations"]


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
