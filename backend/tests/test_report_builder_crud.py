from __future__ import annotations

import pytest

from app.services.reports import _excel_safe
from tests._report_builder_helpers import (
    PASSWORD,
    _register_and_login,
    _upload,
    _create_chart,
    _report_body,
)

pytestmark = pytest.mark.asyncio


def test_excel_safe_quotes_formulas_but_not_negative_numbers():
    assert _excel_safe("=cmd|'/c calc'!A0") == "'=cmd|'/c calc'!A0"
    assert _excel_safe("-5.20") == "-5.20"
    assert _excel_safe("-1,234.56") == "-1,234.56"
    assert _excel_safe("+15.0") == "+15.0"


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
                hashed_password=hash_password(PASSWORD),
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


async def test_report_name_cannot_be_blank(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/reports/custom", headers=headers, json={"name": "", "sections": []})
    assert res.status_code == 422, res.text
