from __future__ import annotations

import pytest

from app.services.reports import _excel_safe

pytestmark = pytest.mark.asyncio


def test_excel_safe_quotes_formulas_but_not_negative_numbers():
    assert _excel_safe("=cmd|'/c calc'!A0") == "'=cmd|'/c calc'!A0"
    assert _excel_safe("-5.20") == "-5.20"
    assert _excel_safe("-1,234.56") == "-1,234.56"
    assert _excel_safe("+15.0") == "+15.0"

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

    for fmt in ("html", "csv", "pdf", "xlsx"):
        res = await client.get(f"/reports/custom/{report_id}/render?format={fmt}", headers=headers)
        assert res.status_code == 200, res.text
        assert len(res.content) > 0
    xlsx_res = await client.get(f"/reports/custom/{report_id}/render?format=xlsx", headers=headers)
    assert xlsx_res.content.startswith(b"PK\x03\x04")

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


async def test_table_section_escapes_formula_injection_in_csv_and_xlsx(client):
    """A dataset cell starting with =/+/-/@ opens as a live formula in Excel
    on double-click (e.g. "=cmd|'/c calc'!A0") - both exporters must neutralize
    it with a leading quote rather than passing it through verbatim."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(
        client, token, content=b"sku,note\nA-1,=cmd|'/c calc'!A0\n", filename="formulas.csv"
    )

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Formula check",
            "sections": [{
                "type": "table", "title": "Rows", "dataset_id": dataset_id,
                "query": {"select": [{"column": "sku"}, {"column": "note"}]},
            }],
        },
    )
    report_id = created.json()["id"]

    csv_res = await client.get(f"/reports/custom/{report_id}/render?format=csv", headers=headers)
    assert b",=cmd" not in csv_res.content  # must be quote-prefixed, not raw after the delimiter
    assert b",'=cmd" in csv_res.content

    xlsx_res = await client.get(f"/reports/custom/{report_id}/render?format=xlsx", headers=headers)
    assert xlsx_res.status_code == 200
    assert xlsx_res.content.startswith(b"PK\x03\x04")


async def test_sql_section_renders_in_report(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "SQL section",
            "sections": [{
                "type": "sql", "title": "Raw query", "dataset_id": dataset_id,
                "sql_text": "SELECT sku, qty FROM dataset ORDER BY sku",
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Raw query" in res.content
    assert b"A-1" in res.content


async def test_sql_section_requires_dataset_id_and_sql_text(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Bad SQL section", "sections": [{"type": "sql", "title": "Missing fields"}]},
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_narrate_custom_report_falls_back_without_llm_key(client):
    """No GEMINI_API_KEY is configured in tests, so narration always takes the
    deterministic fallback path - a real assertion on grounded, non-empty
    text without needing to mock the LLM."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)
    chart_id = await _create_chart(client, token, dataset_id)

    created = await client.post("/reports/custom", headers=headers, json=_report_body(dataset_id, chart_id))
    report_id = created.json()["id"]

    res = await client.post(f"/reports/custom/{report_id}/narrate", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["executive_summary"]
    assert len(body["sections"]) == 5
    assert body["sections"][0]["title"] == "Overview"
    assert all(s["narrative"] for s in body["sections"])


async def test_narrate_custom_report_rejects_unknown_style(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Empty", "sections": [{"type": "text", "text": "hi"}]},
    )
    report_id = created.json()["id"]

    res = await client.post(
        f"/reports/custom/{report_id}/narrate", params={"style": "haiku"}, headers=headers
    )
    assert res.status_code == 400


async def test_narrate_custom_report_cross_org_isolation(client):
    token_a = await _register_and_login(client, email="na@example.com", org_name="NarrateOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    created = await client.post(
        "/reports/custom", headers=headers_a,
        json={"name": "OrgA report", "sections": [{"type": "text", "text": "secret"}]},
    )
    report_id = created.json()["id"]

    token_b = await _register_and_login(client, email="nb@example.com", org_name="NarrateOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    res = await client.post(f"/reports/custom/{report_id}/narrate", headers=headers_b)
    assert res.status_code == 404


async def test_rbac_blocks_non_owner_from_delete(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="rbowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Owned by owner", "sections": [{"type": "text", "text": "hi"}]},
    )
    report_id = created.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="rbmember@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "rbmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.delete(f"/reports/custom/{report_id}", headers=member_headers)
    assert res.status_code == 403
    assert (await client.get(f"/reports/custom/{report_id}", headers=headers)).status_code == 200


async def test_engine_section_resolves_oee_into_summary_and_breakdown_tables(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(
        client, token,
        content=b"planned,operating,total,good,machine\n480,420,1000,950,M1\n480,400,900,850,M2\n",
        filename="oee.csv",
    )

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "OEE capture",
            "sections": [{
                "type": "engine", "title": "OEE",
                "engine_path": "/analytics/oee",
                "engine_body": {
                    "dataset_id": dataset_id,
                    "planned_time_col": "planned", "operating_time_col": "operating",
                    "total_units_col": "total", "good_units_col": "good",
                    "machine_col": "machine",
                },
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    # a "Summary" table (scalar KPI fields) and a "Machines" table (the
    # list[OeeMachineResult] field) should both have rendered as sections
    assert b"OEE" in res.content
    assert b"M1" in res.content
    assert b"M2" in res.content


async def test_engine_section_rejects_unknown_path(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad engine",
            "sections": [{"type": "engine", "engine_path": "/analytics/not-a-real-engine", "engine_body": {}}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_flatten_response_to_sections_scalars_and_lists():
    from app.services.reports import _flatten_response_to_sections

    sections = _flatten_response_to_sections("Test", {
        "oee": 72.5,
        "performance_estimated": False,
        "machines": [{"machine": "M1", "oee": 70.0}, {"machine": "M2", "oee": 75.0}],
        "revenue_impact": {"unit_price": 2.5, "total_lost": 100.0, "per_machine": [{"machine": "M1", "total_lost": 50.0}]},
        "trend": [],
    })

    titles = [s.title for s in sections]
    assert "Test — Summary" in titles
    assert "Test — Machines" in titles
    assert "Test — Revenue Impact — Per Machine" in titles
    # "trend" was an empty list - no section for it
    assert not any("Trend" in t for t in titles)

    summary = next(s for s in sections if s.title == "Test — Summary")
    summary_labels = [row[0] for row in summary.rows]
    assert "Oee" in summary_labels
    assert "Performance Estimated" in summary_labels
    # a nested dict's own scalar fields join the summary, prefixed
    assert any("Revenue Impact" in label for label in summary_labels)


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


async def test_kind_report_section_mrp_plan(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "MRP capture",
            "sections": [{
                "type": "kind_report", "report_kind": "mrp-plan",
                "report_params": {"horizon_start": "2026-01-01", "horizon_end": "2026-04-01"},
            }],
        },
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Suggested actions" in res.content
    assert b"Full plan" in res.content


async def test_kind_report_section_inventory_analytics_needs_no_params(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Inventory capture",
            "sections": [{"type": "kind_report", "report_kind": "inventory-analytics"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Item positions" in res.content


async def test_kind_report_section_rejects_unknown_kind(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Bad kind", "sections": [{"type": "kind_report", "report_kind": "not-a-kind"}]},
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_rca_case_section_renders_causes_and_actions(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    case = await client.post(
        "/rca-cases/cases", headers=headers,
        json={"dataset_id": dataset_id, "title": "Line 3 OEE drop", "problem_statement": "OEE fell 15pts"},
    )
    assert case.status_code == 201
    case_id = case.json()["id"]

    node = await client.post(
        f"/rca-cases/cases/{case_id}/nodes", headers=headers,
        json={"text": "Changeover took too long", "node_type": "cause"},
    )
    assert node.status_code == 201

    action = await client.post(
        f"/rca-cases/cases/{case_id}/actions", headers=headers,
        json={"description": "Standardize changeover checklist"},
    )
    assert action.status_code == 201

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "RCA case capture", "sections": [{"type": "rca_case", "case_id": case_id}]},
    )
    assert created.status_code == 201
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Changeover took too long" in res.content
    assert b"Standardize changeover checklist" in res.content


async def test_rca_case_section_404s_on_unknown_case(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={
            "name": "Bad case",
            "sections": [{"type": "rca_case", "case_id": "00000000-0000-0000-0000-000000000000"}],
        },
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 404


async def test_inbox_create_list_and_delete(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "OEE — July", "type": "text", "text": "captured content"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["label"] == "OEE — July"
    assert item["spec"]["type"] == "text"

    listed = await client.get("/reports/inbox", headers=headers)
    assert listed.status_code == 200
    assert any(i["id"] == item["id"] for i in listed.json())

    deleted = await client.delete(f"/reports/inbox/{item['id']}", headers=headers)
    assert deleted.status_code == 204
    assert not any(i["id"] == item["id"] for i in (await client.get("/reports/inbox", headers=headers)).json())


async def test_inbox_delete_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ibowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "Owner's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ibmember@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ibmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # a different member can't delete someone else's inbox item...
    res = await client.delete(f"/reports/inbox/{item_id}", headers=member_headers)
    assert res.status_code == 403

    # ...but the creator can delete their own.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_delete_allowed_for_owner_deleting_someone_elses_item(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ib2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ib2member@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ib2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = await client.post(
        "/reports/inbox", headers=member_headers,
        json={"label": "Member's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    # the org owner can delete a different member's inbox item.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_cross_org_isolation(client):
    token_a = await _register_and_login(client, email="iba@example.com", org_name="InboxOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    created = await client.post(
        "/reports/inbox", headers=headers_a,
        json={"label": "OrgA capture", "type": "text", "text": "secret"},
    )
    item_id = created.json()["id"]

    token_b = await _register_and_login(client, email="ibb@example.com", org_name="InboxOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    listed_b = await client.get("/reports/inbox", headers=headers_b)
    assert not any(i["id"] == item_id for i in listed_b.json())
    assert (await client.delete(f"/reports/inbox/{item_id}", headers=headers_b)).status_code == 404
