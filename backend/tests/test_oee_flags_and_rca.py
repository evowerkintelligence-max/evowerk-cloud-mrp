from __future__ import annotations

import pytest

from tests._oee_helpers import (
    _register_and_login,
    _upload_dataset,
    SWAPPED_COLUMNS_CSV,
    DROP_PERIOD_CSV,
    DATE_GAP_CSV,
    DUPLICATE_ROWS_CSV,
    LOW_SAMPLE_CSV,
)

pytestmark = pytest.mark.asyncio


async def test_oee_rca_ranks_worst_machine_first(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        "/analytics/oee/rca",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    body = res.json()
    machine_dim = next(d for d in body["dimensions"] if d["name"] == "Machine")
    oees = [r["oee"] for r in machine_dim["rows"]]
    assert oees == sorted(oees)  # worst (lowest) OEE first


async def test_oee_flags_clamped_availability_and_quality(client):
    """operating > planned and good > total are physically impossible — almost
    always a sign the column mapping is swapped. Availability/Quality still get
    clamped to 100% (compute_components_ideal's existing guard), but the response
    must flag it instead of showing a confident, unqualified 100%."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("swapped.csv", SWAPPED_COLUMNS_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["availability_clamped"] is True
    assert body["quality_clamped"] is True
    assert body["availability"] == 100.0
    assert body["quality"] == 100.0

    # The healthy dataset from the first test must NOT trip these flags.
    dataset_id = await _upload_dataset(client, token)
    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["availability_clamped"] is False
    assert body["quality_clamped"] is False


async def test_oee_rca_drop_periods_finds_the_bad_day(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("drop_period.csv", DROP_PERIOD_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/oee/rca",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_oee"] == 83.33  # operating 250/300 planned, quality 100%

    assert body["drop_periods_total"] == 1
    assert len(body["drop_periods"]) == 1
    drop = body["drop_periods"][0]
    assert drop["date"] == "2026-02-02"
    assert drop["oee"] == 50.0
    assert drop["delta_oee"] == -33.33


async def test_oee_flags_date_range_gaps(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("date_gap.csv", DATE_GAP_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
        },
    )
    assert res.status_code == 200
    assert res.json()["date_gap_days"] == 1  # 2026-03-02 has no rows

    # The healthy, gap-free dataset must not trip the flag.
    dataset_id = await _upload_dataset(client, token)
    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
        },
    )
    assert res.status_code == 200
    assert res.json()["date_gap_days"] == 0


async def test_oee_flags_duplicate_rows(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("dupes.csv", DUPLICATE_ROWS_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "date_col": "date",
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    assert res.json()["duplicate_rows"] == 1


async def test_oee_rca_flags_low_sample_dimension_rows(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("low_sample.csv", LOW_SAMPLE_CSV, "text/csv")}
    )
    assert res.status_code == 201
    dataset_id = res.json()["id"]

    res = await client.post(
        "/analytics/oee/rca",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "planned_min",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    machine_dim = next(d for d in res.json()["dimensions"] if d["name"] == "Machine")
    rows = {r["label"]: r for r in machine_dim["rows"]}
    assert rows["M1"]["row_count"] == 6
    assert rows["M1"]["low_sample"] is False
    assert rows["M2"]["row_count"] == 2
    assert rows["M2"]["low_sample"] is True
