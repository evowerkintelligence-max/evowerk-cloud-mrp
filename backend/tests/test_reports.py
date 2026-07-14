from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme Manufacturing"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create_item(client, headers, sku, name, *, supplier_id=None, lead_time_days=None):
    body = {
        "sku": sku,
        "name": name,
        "item_type": "raw_material",
        "unit_of_measure": "ea",
        "is_lot_tracked": False,
    }
    if supplier_id:
        body["preferred_supplier_id"] = supplier_id
    if lead_time_days is not None:
        body["lead_time_days"] = lead_time_days
    res = await client.post("/items", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _seed_demand(client, headers, item_id, quantity="100"):
    """A forecast inside the default 90-day horizon gives the MRP plan a line
    to report on (and, with no stock, a real shortfall)."""
    res = await client.post(
        "/forecasts",
        json={"item_id": item_id, "period_start": date.today().isoformat(), "quantity": quantity},
        headers=headers,
    )
    assert res.status_code == 200, res.text


ALL_KINDS = ["mrp-plan", "inventory-analytics", "rca", "scenario"]


def _params(kind: str, item_id: str) -> dict:
    if kind == "scenario":
        return {"lead_times": f"{item_id}:21"}
    return {}


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize(
    "fmt,media_type",
    [
        ("html", "text/html"),
        ("csv", "text/csv"),
        ("pdf", "application/pdf"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_every_kind_renders_in_every_format(client, kind, fmt, media_type):
    _, headers = await _register_and_login(client)
    item = await _create_item(client, headers, "WIDGET-1", "Widget", lead_time_days=7)
    await _seed_demand(client, headers, item["id"])

    res = await client.get(f"/reports/{kind}", params={"format": fmt, **_params(kind, item["id"])}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(media_type)
    assert f'.{fmt}"' in res.headers["content-disposition"]
    assert len(res.content) > 0

    if fmt == "pdf":
        assert res.content.startswith(b"%PDF-")
    if fmt == "html":
        assert res.text.startswith("<!doctype html>")
        assert "Acme Manufacturing" in res.text
    if fmt == "csv":
        # utf-8-sig BOM so Excel on Windows reads it as UTF-8.
        assert res.content.startswith(b"\xef\xbb\xbf")
    if fmt == "xlsx":
        # xlsx is a zip archive - PK magic bytes confirm openpyxl wrote a real workbook.
        assert res.content.startswith(b"PK\x03\x04")


async def test_mrp_plan_report_contains_the_shortfall(client):
    _, headers = await _register_and_login(client)
    item = await _create_item(client, headers, "BOLT-9", "Hex Bolt")
    await _seed_demand(client, headers, item["id"], quantity="250")

    res = await client.get("/reports/mrp-plan", params={"format": "csv"}, headers=headers)
    rows = list(csv.reader(io.StringIO(res.content.decode("utf-8-sig"))))
    flat = [cell for row in rows for cell in row]

    assert "BOLT-9" in flat
    assert "Order" in flat  # purchased item, so the suggested action is a buy
    assert "250.00" in flat  # nothing on hand or on order: net == gross
    # The HTML rendering of the same data must agree.
    html_res = await client.get("/reports/mrp-plan", params={"format": "html"}, headers=headers)
    assert "BOLT-9" in html_res.text
    assert "Hex Bolt" in html_res.text


async def test_mrp_plan_report_honours_the_horizon(client):
    """Demand outside the requested horizon must not appear - the report is
    built from the same netting as GET /mrp/plan, not a separate query."""
    _, headers = await _register_and_login(client)
    item = await _create_item(client, headers, "FAR-1", "Distant Widget")
    far_future = (date.today() + timedelta(days=200)).isoformat()
    res = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": far_future, "quantity": "50"},
        headers=headers,
    )
    assert res.status_code == 200

    inside = await client.get(
        "/reports/mrp-plan",
        params={"format": "csv", "horizon_start": date.today().isoformat(), "horizon_end": far_future},
        headers=headers,
    )
    outside = await client.get("/reports/mrp-plan", params={"format": "csv"}, headers=headers)

    assert "FAR-1" in inside.content.decode("utf-8-sig")
    assert "FAR-1" not in outside.content.decode("utf-8-sig")


async def test_html_escapes_org_and_item_names(client):
    """Item names are user input rendered straight into a report - they must
    never be able to close a tag."""
    _, headers = await _register_and_login(client, org_name="<script>alert(1)</script>")
    await _create_item(client, headers, "XSS-1", '"><img src=x onerror=alert(1)>')

    res = await client.get("/reports/inventory-analytics", params={"format": "html"}, headers=headers)
    assert res.status_code == 200
    assert "<script>alert(1)</script>" not in res.text
    assert "<img src=x" not in res.text
    assert "&lt;script&gt;" in res.text


async def test_scenario_report_needs_lead_times(client):
    _, headers = await _register_and_login(client)
    res = await client.get("/reports/scenario", params={"format": "html"}, headers=headers)
    assert res.status_code == 400


@pytest.mark.parametrize(
    "lead_times,expected",
    [
        ("not-a-uuid:7", 400),
        ("00000000-0000-0000-0000-000000000001:7", 404),  # well-formed, wrong org
        ("", 400),
    ],
)
async def test_scenario_report_rejects_bad_lead_times(client, lead_times, expected):
    _, headers = await _register_and_login(client)
    res = await client.get(
        "/reports/scenario", params={"format": "html", "lead_times": lead_times}, headers=headers
    )
    assert res.status_code == expected


async def test_scenario_report_rejects_negative_lead_time(client):
    _, headers = await _register_and_login(client)
    item = await _create_item(client, headers, "NEG-1", "Negative")
    res = await client.get(
        "/reports/scenario", params={"format": "html", "lead_times": f"{item['id']}:-1"}, headers=headers
    )
    assert res.status_code == 400


async def test_scenario_report_missing_colon_is_rejected(client):
    _, headers = await _register_and_login(client)
    item = await _create_item(client, headers, "NOCOLON-1", "No Colon")
    res = await client.get(
        "/reports/scenario", params={"format": "html", "lead_times": item["id"]}, headers=headers
    )
    assert res.status_code == 400


DATASET_CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _upload_dataset(client, headers, *, content=DATASET_CSV, filename="stock.csv"):
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_dataset_report_needs_dataset_id(client):
    _, headers = await _register_and_login(client)
    res = await client.get("/reports/dataset", params={"format": "html"}, headers=headers)
    assert res.status_code == 400


async def test_dataset_report_unknown_dataset_404(client):
    _, headers = await _register_and_login(client)
    res = await client.get(
        "/reports/dataset",
        params={"format": "html", "dataset_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert res.status_code == 404


async def test_dataset_report_shows_preview_and_kpis(client):
    _, headers = await _register_and_login(client)
    dataset_id = await _upload_dataset(client, headers)

    csv_res = await client.get(
        "/reports/dataset", params={"format": "csv", "dataset_id": dataset_id}, headers=headers
    )
    assert csv_res.status_code == 200, csv_res.text
    rows = list(csv.reader(io.StringIO(csv_res.content.decode("utf-8-sig"))))
    flat = [cell for row in rows for cell in row]
    assert "A-1" in flat and "east" in flat  # preview rows made it in
    assert "3" in flat  # Rows KPI

    html_res = await client.get(
        "/reports/dataset", params={"format": "html", "dataset_id": dataset_id}, headers=headers
    )
    assert "A-1" in html_res.text


async def test_dataset_report_cross_tenant_isolation(client):
    _, headers_a = await _register_and_login(client, email="dra@example.com", org_name="DR Org A")
    dataset_id = await _upload_dataset(client, headers_a)

    _, headers_b = await _register_and_login(client, email="drb@example.com", org_name="DR Org B")
    res = await client.get(
        "/reports/dataset", params={"format": "html", "dataset_id": dataset_id}, headers=headers_b
    )
    assert res.status_code == 404


async def test_rca_report_rejects_unknown_dimension(client):
    _, headers = await _register_and_login(client)
    res = await client.get("/reports/rca", params={"format": "html", "dimension": "bogus"}, headers=headers)
    assert res.status_code == 400


async def test_unknown_kind_and_format_are_rejected(client):
    _, headers = await _register_and_login(client)
    assert (await client.get("/reports/nope", headers=headers)).status_code == 404
    assert (
        await client.get("/reports/rca", params={"format": "docx"}, headers=headers)
    ).status_code == 400


async def test_reports_require_auth(client):
    assert (await client.get("/reports/inventory-analytics")).status_code == 401


async def test_org_isolation(client):
    _, headers_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    await _create_item(client, headers_a, "SECRET-1", "Org A Secret Widget")
    _, headers_b = await _register_and_login(client, email="b@example.com", org_name="Org B")

    res = await client.get("/reports/inventory-analytics", params={"format": "html"}, headers=headers_b)
    assert res.status_code == 200
    assert "SECRET-1" not in res.text
    assert "Org B" in res.text


async def test_empty_org_renders_every_format_without_error(client):
    """A brand-new org has no items, no forecasts and no analytics rows. Every
    renderer must still produce a valid document rather than divide by zero."""
    _, headers = await _register_and_login(client)
    for kind in ("mrp-plan", "inventory-analytics", "rca"):
        for fmt in ("html", "csv", "pdf"):
            res = await client.get(f"/reports/{kind}", params={"format": fmt}, headers=headers)
            assert res.status_code == 200, f"{kind}/{fmt}: {res.text}"
            assert len(res.content) > 0
