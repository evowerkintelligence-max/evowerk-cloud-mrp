from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="stock.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def _create_chart(client, token, dataset_id):
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "dataset_id": dataset_id,
        "chart_type": "bar",
        "query": {
            "select": [{"column": "sku"}, {"column": "qty", "agg": "sum", "alias": "total_qty"}],
            "group_by": ["sku"],
            "order_by": "sku",
        },
        "category_field": "sku",
        "value_field": "total_qty",
        "name": "Qty by SKU",
    }
    res = await client.post("/bi/charts", headers=headers, json=body)
    assert res.status_code == 201
    return res.json()["id"]


def _report_body(dataset_id, chart_id):
    return {
        "name": "Weekly ops summary",
        "sections": [
            {"type": "title", "title": "Overview", "text": "Week 12 summary"},
            {"type": "text", "title": "Notes", "text": "Everything nominal.\nNo incidents."},
            {"type": "findings", "title": "Findings", "items": ["Region west trending down"]},
            {
                "type": "table",
                "title": "Stock by region",
                "dataset_id": dataset_id,
                "query": {"select": [{"column": "sku"}, {"column": "region"}, {"column": "qty"}]},
            },
            {"type": "bi_chart", "title": "Qty by SKU", "chart_id": chart_id},
        ],
    }


async def test_create_and_list_custom_report(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)
    chart_id = await _create_chart(client, token, dataset_id)

    created = await client.post("/reports/custom", headers=headers, json=_report_body(dataset_id, chart_id))
    assert created.status_code == 201
    report = created.json()
    assert report["name"] == "Weekly ops summary"
    assert len(report["sections"]) == 5

    listed = await client.get("/reports/custom", headers=headers)
    assert listed.status_code == 200
    assert any(r["id"] == report["id"] for r in listed.json())


async def test_render_custom_report_all_formats(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)
    chart_id = await _create_chart(client, token, dataset_id)

    created = await client.post("/reports/custom", headers=headers, json=_report_body(dataset_id, chart_id))
    report_id = created.json()["id"]

    for fmt in ("html", "csv", "pdf"):
        res = await client.get(f"/reports/custom/{report_id}/render?format={fmt}", headers=headers)
        assert res.status_code == 200, res.text
        assert len(res.content) > 0

    html_body = (await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)).content
    assert b"Weekly ops summary" in html_body
    assert b"Qty by SKU" in html_body


async def test_update_and_delete_custom_report(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)
    chart_id = await _create_chart(client, token, dataset_id)

    created = await client.post("/reports/custom", headers=headers, json=_report_body(dataset_id, chart_id))
    report_id = created.json()["id"]

    updated = await client.put(
        f"/reports/custom/{report_id}",
        headers=headers,
        json={"name": "Renamed", "sections": [{"type": "text", "text": "Just a note"}]},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert len(updated.json()["sections"]) == 1

    deleted = await client.delete(f"/reports/custom/{report_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/reports/custom/{report_id}", headers=headers)).status_code == 404


async def test_bi_chart_section_with_unknown_chart_404s_on_render(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom",
        headers=headers,
        json={
            "name": "Broken",
            "sections": [{"type": "bi_chart", "chart_id": "00000000-0000-0000-0000-000000000000"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 404


async def test_cross_org_isolation_on_custom_report(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    created = await client.post(
        "/reports/custom",
        headers=headers_a,
        json={"name": "OrgA report", "sections": [{"type": "text", "text": "secret"}]},
    )
    report_id = created.json()["id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    assert (await client.get(f"/reports/custom/{report_id}", headers=headers_b)).status_code == 404
    assert (
        await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers_b)
    ).status_code == 404
    assert (await client.delete(f"/reports/custom/{report_id}", headers=headers_b)).status_code == 404
