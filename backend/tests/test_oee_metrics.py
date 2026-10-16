from __future__ import annotations

import pytest

from tests._oee_helpers import (
    _register_and_login,
    _upload_dataset,
    SIX_LOSSES_CSV,
    RATE_BLEND_CSV,
)

pytestmark = pytest.mark.asyncio


async def test_oee_compute_availability_performance_quality(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
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
            "machine_col": "machine",
        },
    )
    assert res.status_code == 200
    body = res.json()

    total_planned = 480 * 4
    total_operating = 440 + 400 + 460 + 420
    total_units = 880 + 760 + 900 + 800
    good_units = 850 + 700 + 880 + 760

    assert body["total_planned"] == total_planned
    assert body["total_operating"] == total_operating
    assert body["availability"] == round(total_operating / total_planned * 100, 2)
    # No target_rate/machine_rates supplied -> performance is fully estimated at 100%.
    assert body["performance"] == 100.0
    assert body["performance_estimated"] is True
    assert body["quality"] == round(good_units / total_units * 100, 2)
    assert len(body["machines"]) == 2
    assert len(body["trend"]) == 2


async def test_oee_compute_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    res = await client.post(
        "/analytics/oee",
        headers=headers,
        json={
            "dataset_id": dataset_id,
            "planned_time_col": "nope",
            "operating_time_col": "operating_min",
            "total_units_col": "total_units",
            "good_units_col": "good_units",
        },
    )
    assert res.status_code == 400


async def test_oee_six_big_losses_splits_when_columns_supplied(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("six_losses.csv", SIX_LOSSES_CSV, "text/csv")}
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
            "target_rate": 1.0,
            "unplanned_downtime_col": "unplanned_dt_min",
            "planned_downtime_col": "planned_dt_min",
            "minor_stops_col": "minor_stops_min",
            "startup_rejects_col": "startup_rejects_units",
        },
    )
    assert res.status_code == 200
    six = res.json()["six_big_losses"]
    assert six is not None
    assert six["availability_split"] is True
    assert six["performance_split"] is True
    assert six["quality_split"] is True
    assert six["total_loss_minutes"] == 180.0

    buckets = {b["key"]: b for b in six["buckets"]}
    assert set(buckets) == {
        "equipment_failure", "setup_adjustments", "unclassified_downtime",
        "minor_stops", "reduced_speed",
        "startup_rejects", "process_defects",
    }
    assert buckets["equipment_failure"]["minutes"] == 60.0
    assert buckets["setup_adjustments"]["minutes"] == 30.0
    assert buckets["unclassified_downtime"]["minutes"] == 10.0
    assert buckets["minor_stops"]["minutes"] == 20.0
    assert buckets["reduced_speed"]["minutes"] == 30.0
    assert buckets["startup_rejects"]["minutes"] == 10.0
    assert buckets["startup_rejects"]["units"] == 10.0
    assert buckets["process_defects"]["minutes"] == 20.0
    assert buckets["process_defects"]["units"] == 20.0


async def test_oee_teep_and_utilization(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)  # OEE_CSV: 2 machines x 2 days

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
    body = res.json()
    assert body["oee"] == 85.56  # availability 1720/1920, performance 100 (estimated), quality 3190/3340

    teep = body["teep"]
    assert teep is not None
    assert teep["calendar_days"] == 2
    assert teep["machine_count"] == 2
    assert teep["machine_count_assumed"] is False
    assert teep["utilization"] == 33.33  # planned 1920 / (2 days * 1440 min * 2 machines)
    assert teep["teep"] == 28.52  # oee(85.56) * utilization(0.3333)


async def test_oee_revenue_impact(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)  # OEE_CSV

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
            "unit_price": 2.0,
        },
    )
    assert res.status_code == 200
    ri = res.json()["revenue_impact"]
    assert ri is not None
    # No target_rate -> fully estimated -> ideal == total, so performance_lost is $0
    # and every unit of the availability gap is priced at the observed rate.
    assert ri["performance_lost"] == 0.0
    assert ri["quality_lost"] == 300.0  # (3340 total - 3190 good) * $2
    assert ri["availability_lost"] == 776.74  # (1920 planned - 1720 operating) * (3340/1720) * $2
    assert ri["total_lost"] == 1076.74
    assert ri["period_days"] == 2.0
    assert ri["period_days_estimated"] is False
    assert ri["daily_rate"] == 538.37
    assert ri["monthly_rate"] == 16151.1


async def test_oee_per_machine_rate_blending_is_not_a_flat_average(client):
    """Overall Performance must come from SUM(ideal units) not AVG(per-machine
    performance) — a machine with more operating time should carry more weight
    in the blend, and the two must differ from a naive 80/75 average."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": ("rate_blend.csv", RATE_BLEND_CSV, "text/csv")}
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
            "machine_col": "machine",
            "machine_rates": {"M1": 1.0, "M2": 2.0},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["performance_estimated"] is False
    # M1 ideal = 100*1=100, actual 80 -> 80%. M2 ideal = 200*2=400, actual 300 -> 75%.
    # Blended = (80+300)/(100+400) = 76%, not the flat average of 77.5%.
    assert body["performance"] == 76.0
    assert body["availability"] == 100.0
    assert body["quality"] == 100.0
    assert body["oee"] == 76.0

    machines = {m["machine"]: m for m in body["machines"]}
    assert machines["M1"]["performance"] == 80.0
    assert machines["M1"]["rate_used"] == 1.0
    assert machines["M2"]["performance"] == 75.0
    assert machines["M2"]["rate_used"] == 2.0


async def test_oee_rejects_non_positive_target_rate_and_unit_price(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token)

    base = {
        "dataset_id": dataset_id,
        "planned_time_col": "planned_min",
        "operating_time_col": "operating_min",
        "total_units_col": "total_units",
        "good_units_col": "good_units",
    }
    res = await client.post("/analytics/oee", headers=headers, json={**base, "target_rate": 0})
    assert res.status_code == 422
    res = await client.post("/analytics/oee", headers=headers, json={**base, "target_rate": -5})
    assert res.status_code == 422
    res = await client.post("/analytics/oee", headers=headers, json={**base, "unit_price": 0})
    assert res.status_code == 422
