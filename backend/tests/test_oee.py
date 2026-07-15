from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

OEE_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units\n"
    b"2026-01-01,M1,480,440,880,850\n"
    b"2026-01-01,M2,480,400,760,700\n"
    b"2026-01-02,M1,480,460,900,880\n"
    b"2026-01-02,M2,480,420,800,760\n"
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
        "/datasets", headers=headers, files={"file": ("oee.csv", OEE_CSV, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


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


SIX_LOSSES_CSV = (
    b"planned_min,operating_min,total_units,good_units,"
    b"unplanned_dt_min,planned_dt_min,minor_stops_min,startup_rejects_units\n"
    b"500,400,350,320,60,30,20,10\n"
)


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


SWAPPED_COLUMNS_CSV = (
    b"planned_min,operating_min,total_units,good_units\n"
    b"400,500,320,350\n"
)


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


RATE_BLEND_CSV = (
    b"machine,planned_min,operating_min,total_units,good_units\n"
    b"M1,100,100,80,80\n"
    b"M2,200,200,300,300\n"
)


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


DROP_PERIOD_CSV = (
    b"date,planned_min,operating_min,total_units,good_units\n"
    b"2026-02-01,100,100,100,100\n"
    b"2026-02-02,100,50,100,100\n"
    b"2026-02-03,100,100,100,100\n"
)


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


DATE_GAP_CSV = (
    b"date,planned_min,operating_min,total_units,good_units\n"
    b"2026-03-01,100,100,100,100\n"
    b"2026-03-03,100,100,100,100\n"
)


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


DUPLICATE_ROWS_CSV = (
    b"date,machine,planned_min,operating_min,total_units,good_units\n"
    b"2026-04-01,M1,100,90,100,95\n"
    b"2026-04-01,M1,100,90,100,95\n"
    b"2026-04-01,M2,100,80,100,90\n"
)


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


LOW_SAMPLE_CSV = (
    b"machine,planned_min,operating_min,total_units,good_units\n"
    + b"M1,10,10,10,10\n" * 6
    + b"M2,10,5,10,10\n" * 2
)


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
