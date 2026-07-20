from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction
from app.models.inventory_snapshot import InventoryDailySnapshot
from app.services.morning_brief import compute_morning_brief

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_item(client, token, sku="ITEM-1", name="Widget"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _receive(client, token, item, supplier, qty):
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    recv = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": str(qty)}],
        headers=headers,
    )
    assert recv.status_code == 200


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _create_lot_tracked_item(client, token, sku="LOT-1", name="Chemical"):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "raw_material",
            "unit_of_measure": "ea",
            "is_lot_tracked": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _receive_lot(client, token, item, supplier, qty, lot_number, expiry_date=None):
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    payload = {"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}
    if expiry_date is not None:
        payload["expiry_date"] = expiry_date.isoformat()
    recv = await client.post(f"/purchase-orders/{po_id}/receive", json=[payload], headers=headers)
    assert recv.status_code == 200

    lots = await client.get(f"/lots?item_id={item['id']}", headers=headers)
    return next(lot for lot in lots.json() if lot["lot_number"] == lot_number)


async def _insert_consumption(
    test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty_by_day_offset: dict
):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for offset, qty in qty_by_day_offset.items():
            session.add(
                InventoryTransaction(
                    org_id=uuid_module.UUID(org_id),
                    item_id=uuid_module.UUID(item_id),
                    location_id=uuid_module.UUID(location_id),
                    transaction_type="consumption",
                    quantity_delta=-Decimal(str(qty)),
                    created_by=uuid_module.UUID(created_by),
                    created_at=now - timedelta(days=offset),
                )
            )
        await session.commit()


async def _insert_snapshot(
    test_engine,
    *,
    org_id: str,
    item_id: str,
    snapshot_date,
    on_hand,
    avg_daily_demand,
    days_of_supply,
    status,
):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            InventoryDailySnapshot(
                org_id=uuid_module.UUID(org_id),
                item_id=uuid_module.UUID(item_id),
                snapshot_date=snapshot_date,
                on_hand=Decimal(str(on_hand)),
                avg_daily_demand=avg_daily_demand,
                days_of_supply=days_of_supply,
                status=status,
            )
        )
        await session.commit()


async def _snapshot_row(test_engine, *, org_id: str, item_id: str, snapshot_date):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            select(InventoryDailySnapshot).where(
                InventoryDailySnapshot.org_id == uuid_module.UUID(org_id),
                InventoryDailySnapshot.item_id == uuid_module.UUID(item_id),
                InventoryDailySnapshot.snapshot_date == snapshot_date,
            )
        )
        return result.scalar_one_or_none()


def _by_sku(brief_json) -> dict:
    return {line["item_sku"]: line for line in brief_json["lines"]}


async def test_stockout_with_no_prior_snapshot_is_new(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)

    # Never received: on_hand stays 0, but real consumption history -> stockout.
    stockout_item = await _create_item(client, token, "STOCKOUT")
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=stockout_item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    # Healthy item: plenty of stock relative to its steady demand.
    healthy_item = await _create_item(client, token, "HEALTHY")
    supplier = await _create_supplier(client, token)
    await _receive(client, token, healthy_item, supplier, 300)
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=healthy_item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    lines = _by_sku(res.json())

    assert "HEALTHY" not in lines  # healthy items aren't in the digest
    stockout = lines["STOCKOUT"]
    assert stockout["status"] == "stockout"
    assert stockout["status_last_week"] is None
    assert stockout["days_of_supply_last_week"] is None
    assert stockout["is_new_this_week"] is True


async def test_todays_snapshot_is_captured_once_and_frozen(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    item = await _create_item(client, token, "EXCESS")
    supplier = await _create_supplier(client, token)

    await _receive(client, token, item, supplier, 10000)
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 1 for d in range(90)},  # constant demand, all 90 days -> "history"
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    assert _by_sku(res.json())["EXCESS"]["status"] == "excess"  # 10000 days of supply

    today = datetime.now(timezone.utc).date()
    snap = await _snapshot_row(test_engine, org_id=org_id, item_id=item["id"], snapshot_date=today)
    assert snap is not None
    assert snap.on_hand == Decimal("10000.0000")

    # Change on-hand after the snapshot was captured; today's frozen row must
    # not move even though a second call recomputes live analytics.
    await _receive(client, token, item, supplier, 500)
    res2 = await client.get("/inventory/morning-brief", headers=headers)
    assert res2.status_code == 200

    snap_again = await _snapshot_row(test_engine, org_id=org_id, item_id=item["id"], snapshot_date=today)
    assert snap_again.on_hand == Decimal("10000.0000")  # unchanged, not 10500


async def test_week_over_week_comparison(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    item = await _create_item(client, token, "LOW")
    supplier = await _create_supplier(client, token)

    await _receive(client, token, item, supplier, 5)
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 1 for d in range(90)},  # mean 1/day -> days_of_supply == 5 -> "low"
    )

    week_ago = datetime.now(timezone.utc).date() - timedelta(days=7)
    await _insert_snapshot(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        snapshot_date=week_ago,
        on_hand=20,
        avg_daily_demand=1.0,
        days_of_supply=20.0,
        status="low",
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    line = _by_sku(res.json())["LOW"]
    assert line["status"] == "low"
    assert line["status_last_week"] == "low"
    assert line["days_of_supply_last_week"] == pytest.approx(20.0)
    assert line["days_of_supply_delta"] == pytest.approx(5.0 - 20.0)
    assert line["is_new_this_week"] is False  # already "low" a week ago


async def test_org_isolation(client, test_engine):
    reg_a, token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    item = await _create_item(client, token_a, "A-ITEM")
    await _insert_consumption(
        test_engine,
        org_id=reg_a["org_id"],
        item_id=item["id"],
        location_id=await _default_location_id(client, token_a),
        created_by=reg_a["user_id"],
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")
    res = await client.get("/inventory/morning-brief", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["lines"] == []


async def test_lines_ranked_by_status_priority_before_days_of_supply(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)

    # "low" has a smaller numeric days_of_supply than nothing here, but the
    # point is status priority wins regardless: stockout must rank first
    # even though it's alphabetically after "low" and has no on-hand qty.
    stockout_item = await _create_item(client, token, "SO-1")
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=stockout_item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    low_item = await _create_item(client, token, "LOW-1")
    supplier = await _create_supplier(client, token)
    await _receive(client, token, low_item, supplier, 5)
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=low_item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 1 for d in range(90)},  # days_of_supply == 5 -> "low"
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    skus = [line["item_sku"] for line in res.json()["lines"]]
    assert skus.index("SO-1") < skus.index("LOW-1")


async def test_lot_alert_on_hold_ranked_before_expiring_soon(client):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    # Expires tomorrow but still active - would sort first on expiry
    # proximity alone. A held lot must still outrank it: it already needs a
    # human decision, an expiring-but-unheld lot doesn't yet.
    expiring_item = await _create_lot_tracked_item(client, token, "EXP-1")
    await _receive_lot(
        client, token, expiring_item, supplier, 10, "LOT-EXP",
        expiry_date=date.today() + timedelta(days=1),
    )

    held_item = await _create_lot_tracked_item(client, token, "HOLD-1")
    held_lot = await _receive_lot(
        client, token, held_item, supplier, 10, "LOT-HOLD",
        expiry_date=date.today() + timedelta(days=365),
    )
    assert (await client.post(f"/lots/{held_lot['id']}/hold", headers=headers)).status_code == 200

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    alerts = res.json()["lot_alerts"]
    assert [a["item_sku"] for a in alerts] == ["HOLD-1", "EXP-1"]
    assert alerts[0]["reason"] == "on_hold"
    assert alerts[1]["reason"] == "expiring_soon"


async def test_lot_alert_excludes_depleted_and_distant_expiry(client):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    depleted_item = await _create_lot_tracked_item(client, token, "DEP-1")
    depleted_lot = await _receive_lot(client, token, depleted_item, supplier, 10, "LOT-DEP")
    assert (
        await client.post(f"/lots/{depleted_lot['id']}/consume", json={"quantity": "10"}, headers=headers)
    ).status_code == 200

    # Active, but expiry is far outside the warning window - not actionable.
    distant_item = await _create_lot_tracked_item(client, token, "FAR-1")
    await _receive_lot(
        client, token, distant_item, supplier, 10, "LOT-FAR",
        expiry_date=date.today() + timedelta(days=365),
    )

    res = await client.get("/inventory/morning-brief", headers=headers)
    assert res.status_code == 200
    assert res.json()["lot_alerts"] == []


async def test_snapshot_write_is_skipped_once_captured(client, test_engine, monkeypatch):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    item = await _create_item(client, token, "ONCE")
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        await compute_morning_brief(session, uuid_module.UUID(org_id))

    # Today's snapshot already exists - a second call must not touch the
    # write path (and therefore must not call commit) at all.
    async with session_factory() as session:

        async def _boom():
            raise AssertionError("commit should not run once today's snapshot is already captured")

        monkeypatch.setattr(session, "commit", _boom)
        await compute_morning_brief(session, uuid_module.UUID(org_id))  # must not raise


async def test_snapshot_commit_failure_does_not_break_the_brief(client, test_engine, monkeypatch):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    location_id = await _default_location_id(client, token)
    item = await _create_item(client, token, "FLAKY")
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:

        async def _boom():
            raise OperationalError("db unavailable", None, BaseException())

        monkeypatch.setattr(session, "commit", _boom)
        # The snapshot write is best-effort - a failed commit must not
        # surface as a broken read. (What matters here is that this doesn't
        # raise; the transaction's own atomicity is SQLAlchemy's contract,
        # not this function's.)
        lines = await compute_morning_brief(session, uuid_module.UUID(org_id))

    assert any(line.item_sku == "FLAKY" for line in lines)
