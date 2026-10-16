from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

# total units = 240, planned = 270 -> efficiency 88.9%.
# periods: 2026-01-01 = 160 (peak), 2026-01-02 = 80. machines: M1=180 (75%), M2=60.
TP_CSV = (
    b"units,planned,machine,day\n"
    b"100,120,M1,2026-01-01\n"
    b"80,100,M1,2026-01-02\n"
    b"60,50,M2,2026-01-01\n"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=TP_CSV):
    res = await client.post("/datasets", files={"file": ("tp.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_throughput_metrics(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(
        "/analytics/throughput",
        json={
            "dataset_id": dataset_id,
            "units_col": "units",
            "planned_units_col": "planned",
            "machine_col": "machine",
            "date_col": "day",
        },
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 240.0
    assert b["total_planned"] == 270.0
    assert b["efficiency_pct"] == 88.9
    assert b["period_count"] == 2
    assert b["avg_units_per_period"] == 120.0
    assert b["peak_units"] == 160.0
    assert b["peak_period"] == "2026-01-01"
    machines = {m["machine"]: m for m in b["by_machine"]}
    assert machines["M1"]["pct_of_total"] == 75.0


async def test_throughput_unknown_optional_column_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "machine_col": "nope"},
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_throughput_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    dataset_id = await _upload(client, token_a)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units"},
        headers=_h(token_b),
    )
    assert res.status_code == 404


async def test_throughput_dataset_not_found(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": str(uuid.uuid4()), "units_col": "units"},
        headers=_h(token),
    )
    assert res.status_code == 404


# Non-ISO dates spanning a year boundary: a naive string sort of "MM/DD/YYYY"
# text would put 2026-01-15 before 2025-12-01 (since "01" < "12" lexically),
# which is backwards. date_bucket_expr must normalize to ISO before grouping
# so both the bucketing and the trend's chronological sort come out right.
NON_ISO_CSV = (
    b"units,day\n"
    b"70,01/15/2026\n"
    b"50,12/01/2025\n"
)


async def test_throughput_non_iso_date_grouping_and_sort(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, data=NON_ISO_CSV)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "date_col": "day"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["period_count"] == 2
    assert b["unparsed_date_rows"] == 0
    periods = [t["period"] for t in b["trend"]]
    assert periods == ["2025-12-01", "2026-01-15"]  # chronological, not string order


async def test_throughput_unparsed_date_falls_back_without_dropping_row(client):
    token = await _register_and_login(client)
    csv = b"units,day\n100,not-a-date\n"
    dataset_id = await _upload(client, token, data=csv)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "date_col": "day"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 100.0  # row still counted overall
    assert b["unparsed_date_rows"] == 1
    assert b["period_count"] == 1  # still bucketed (raw-text fallback), not dropped


# -50 is physically impossible for a units-produced column; it must be floored
# to 0 and counted as a data-quality issue (matching OEE's val_expr/inv_expr
# convention), not silently subtracted from the total.
NEGATIVE_CSV = b"units\n100\n-50\n30\n"


async def test_throughput_negative_units_floored_and_flagged(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, data=NEGATIVE_CSV)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 130.0  # 100 + 0 (floored) + 30, not 80
    assert b["invalid_cells"] == 1
    assert b["rows_with_issues"] == 1


async def test_throughput_outlier_value_does_not_break_aggregation(client):
    token = await _register_and_login(client)
    csv = b"units\n10\n999999999\n"
    dataset_id = await _upload(client, token, data=csv)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["total_units"] == 1000000009.0
    assert b["peak_units"] == 1000000009.0  # single-period case: peak = total
    assert b["invalid_cells"] == 0  # a huge number is still a valid number


# No planned_units_col mapped: the service should suggest a flat historical-
# average target instead of leaving efficiency/plan blank.
BASELINE_CSV = (
    b"units,day\n"
    b"100,2026-01-01\n"
    b"200,2026-01-02\n"
    b"150,2026-01-03\n"
)


async def test_throughput_auto_baseline_when_no_plan_column(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, data=BASELINE_CSV)
    res = await client.post(
        "/analytics/throughput",
        json={"dataset_id": dataset_id, "units_col": "units", "date_col": "day"},
        headers=_h(token),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["is_target_estimated"] is True
    assert b["total_planned"] == 450.0  # avg (150) * 3 periods
    assert b["efficiency_pct"] == 100.0  # total vs itself
    assert all(t["planned"] == 150.0 for t in b["trend"])
    # first period ran below the baseline average
    assert b["trend"][0]["efficiency_pct"] == pytest.approx(66.7, abs=0.1)
