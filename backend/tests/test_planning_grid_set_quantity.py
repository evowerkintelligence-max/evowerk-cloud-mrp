from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.planning_grid import GRID_HORIZON_DAYS
from tests._planning_grid_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _plan,
    _grid,
    _product,
    _today,
)

pytestmark = pytest.mark.asyncio


async def test_setting_a_quantity_replaces_the_previous_one(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10, needed_by=str(_today() + timedelta(days=5)))
    await _plan(client, token, widget, 25, needed_by=str(_today() + timedelta(days=30)))
    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_qty"] == "25.0000", "the window must hold one number, not a running total"
    assert row["needed_by"] == str(_today() + timedelta(days=30))


async def test_zero_clears_the_plan(client):
    """WIDGET-1 has no BOM and no consumption history, so clearing its plan to
    0 drops its only claim to a grid row: check the write's own response
    (the fallback row shape), not a follow-up read of the grid."""
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    await _plan(client, token, widget, 10)

    cleared = await _plan(client, token, widget, 0)

    assert cleared["planned_qty"] == "0.0000"
    assert cleared["needed_by"] is None


async def test_quantity_with_no_date_and_no_existing_plan_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "10"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400
    assert "date" in res.json()["detail"].lower()


async def test_quantity_edit_without_a_date_reuses_the_existing_plan_date(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    needed_by = str(_today() + timedelta(days=9))
    await _plan(client, token, widget, 10, needed_by=needed_by)

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "25"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["planned_qty"] == "25.0000"
    assert body["needed_by"] == needed_by


async def test_zero_with_no_date_still_clears_the_plan(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "0"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["planned_qty"] == "0.0000"
    assert res.json()["needed_by"] is None


async def test_planned_bucket_count_is_one_for_a_normally_planned_item(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    await _plan(client, token, widget, 10)
    grid = await _grid(client, token)

    assert _product(grid, "WIDGET-1")["planned_bucket_count"] == 1


async def test_planned_bucket_count_is_more_than_one_for_a_multi_row_plan(client):
    """An imported or legacy forecast can be spread across several dated rows
    inside the window - the frontend needs the count so it can warn before
    editing the quantity collapses them into one."""
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    headers = {"Authorization": f"Bearer {token}"}
    first = str(_today() + timedelta(days=7))
    second = str(_today() + timedelta(days=14))
    for period, qty in [(first, "5"), (second, "8")]:
        res = await client.post(
            "/forecasts",
            json={"item_id": widget["id"], "period_start": period, "quantity": qty},
            headers=headers,
        )
        assert res.status_code == 200, res.text

    grid = await _grid(client, token)

    row = _product(grid, "WIDGET-1")
    assert row["planned_bucket_count"] == 2
    assert row["planned_qty"] == "13.0000"
    assert row["needed_by"] == first


async def test_negative_quantity_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "-5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_oversized_quantity_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "100000000"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_date_outside_the_window_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(_today() + timedelta(days=400))},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_needed_by_of_today_is_accepted(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    plan = await _plan(client, token, widget, 5, needed_by=str(_today()))

    assert plan["needed_by"] == str(_today())


async def test_needed_by_at_the_end_of_the_horizon_is_accepted(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    edge = _today() + timedelta(days=GRID_HORIZON_DAYS)

    plan = await _plan(client, token, widget, 5, needed_by=str(edge))

    assert plan["needed_by"] == str(edge)


async def test_needed_by_before_today_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(_today() - timedelta(days=1))},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_needed_by_one_day_past_the_horizon_is_rejected(client):
    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    past_edge = _today() + timedelta(days=GRID_HORIZON_DAYS + 1)

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5", "needed_by": str(past_edge)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400


async def test_unknown_item_is_rejected(client):
    token = await _register_and_login(client)

    res = await client.put(
        "/planning/grid/00000000-0000-0000-0000-000000000000",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 404


async def test_another_orgs_item_cannot_be_planned(client):
    token_a = await _register_and_login(client)
    widget = await _create_item(client, token_a, "WIDGET-1")
    token_b = await _register_and_login(client, email="other@example.com", org_name="Other")

    res = await client.put(
        f"/planning/grid/{widget['id']}",
        json={"quantity": "5"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert res.status_code == 404


async def test_a_bom_component_with_consumption_history_still_does_not_get_its_own_row(client):
    """Consumption history widens which items count as products, but a
    component of an active BOM must never surface as one - it is still
    reachable only through its parent's row."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-V2")
    pump = await _create_item(client, token, "PUMP-A2")
    await _create_active_bom(client, token, pump, [(seal, 2)])
    await client.post(
        f"/inventory/{seal['id']}/adjust",
        json={"quantity": "1000", "reason_code": "found_stock"},
        headers=headers,
    )
    consume = await client.post(
        f"/inventory/{seal['id']}/consume", json={"quantity": "7"}, headers=headers
    )
    assert consume.status_code == 200, consume.text

    grid = await _grid(client, token)

    assert {row["item_sku"] for row in grid["products"]} == {"PUMP-A2"}


async def test_set_planned_quantity_audit_log_carries_quantity_needed_by_and_rows_replaced(
    client, test_engine
):
    """The single delete-then-insert write this endpoint makes must be
    reconstructible from the audit log alone, same as the bucket endpoint it
    replaced (see forecasts.py)."""
    from sqlalchemy import select

    from app.models.audit import AuditLog

    token = await _register_and_login(client)
    widget = await _create_item(client, token, "WIDGET-1")
    needed_by = str(_today() + timedelta(days=10))
    await _plan(client, token, widget, 10, needed_by=needed_by)
    await _plan(client, token, widget, 25, needed_by=str(_today() + timedelta(days=20)))

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "forecast.planned_via_grid")
            )
        ).scalars().all()

    events = sorted(rows, key=lambda r: r.created_at)
    assert len(events) == 2
    first, second = events
    assert first.event_metadata["quantity"] == "10"
    assert first.event_metadata["needed_by"] == needed_by
    assert first.event_metadata["rows_replaced"] == 0, "nothing existed yet to replace"
    assert second.event_metadata["quantity"] == "25"
    assert second.event_metadata["rows_replaced"] == 1, "the previous plan's one row must be counted"
