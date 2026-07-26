# MRP Phase 1: Surface Forecasting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing demand-forecasting service in the Planning UI: bulk weekly suggestions with a review-and-accept flow, plus CSV forecast import.

**Architecture:** A pure weekly-bucketing function and a bulk-suggestion helper are added to `services/demand_forecasting.py` (one history query for all items, then the existing per-item math). Two new routes in `api/routes/forecasts.py`: `GET /forecasts/suggested` (review payload) and `POST /forecasts/suggested/accept` (server-authoritative, transactional delete-window-then-upsert, `source="suggested"`). The Planning page gains two components backed by pure, unit-tested logic modules: a suggest-from-history card and a CSV importer that posts to the untouched `/forecasts/import`.

**Tech Stack:** FastAPI + SQLAlchemy async + pytest (backend), React 18 + TanStack Query + vitest (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-25-mrp-surface-forecasting-design.md`

## Global Constraints

- Do not change `POST /forecasts/import` request or response shape; the desktop forecaster depends on it.
- Forecast source values are exactly: `manual`, `imported`, `suggested`. `DemandForecast.source` is String(20); no migration needed.
- Accept window is `[as_of + 1 day, as_of + horizon_days]` inclusive; deletes and bucket starts must both derive from the same `as_of` passed in by the route.
- `horizon_days` valid range is 1..365 on every new endpoint (same rule as the existing per-item route).
- Reads need `get_current_user`; forecast writes need `require_role("owner")`.
- Notes stamp format on accepted rows: `from history (<method_used>, <history_points> days)`.
- Backend tests: run with `cd backend && .venv/Scripts/python.exe -m pytest <file> -v`.
- Frontend: `cd frontend && npx vitest run <file>` for tests, `cd frontend && npm run build` for the type gate.
- No em dashes in any prose or comments.
- Frontend components use existing UI primitives (`Card`, `Table`, `Badge`, `Button`, `Checkbox`, `Alert`, `Field`, `Input` from `@/components/ui/*`, `@/components/Field`).

---

### Task 1: Weekly bucketing pure function

**Files:**
- Modify: `backend/app/services/demand_forecasting.py` (append after `DemandForecastResult`)
- Test: `backend/tests/test_forecast_suggestions.py` (new file)

**Interfaces:**
- Consumes: `ForecastPoint` dataclass already defined in `app/services/demand_forecasting.py` (fields `period_start: date`, `yhat: float`, `lower: float | None`, `upper: float | None`).
- Produces: `bucket_weekly(points: list[ForecastPoint], bucket_days: int = 7) -> list[ForecastPoint]`, used by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_forecast_suggestions.py`:

```python
from __future__ import annotations

from datetime import date, timedelta

from app.services.demand_forecasting import ForecastPoint, bucket_weekly


def _daily(n: int, yhat: float = 3.0, with_bounds: bool = True) -> list[ForecastPoint]:
    start = date(2026, 8, 1)
    return [
        ForecastPoint(
            period_start=start + timedelta(days=i),
            yhat=yhat,
            lower=(yhat - 1.0) if with_bounds else None,
            upper=(yhat + 1.0) if with_bounds else None,
        )
        for i in range(n)
    ]


def test_bucket_weekly_sums_full_and_partial_buckets():
    buckets = bucket_weekly(_daily(30))
    assert len(buckets) == 5  # 4 full weeks + one 2-day partial
    assert [b.period_start for b in buckets] == [
        date(2026, 8, 1) + timedelta(days=7 * k) for k in range(5)
    ]
    assert [b.yhat for b in buckets] == [21.0, 21.0, 21.0, 21.0, 6.0]
    # bucket totals preserve the daily total
    assert sum(b.yhat for b in buckets) == sum(p.yhat for p in _daily(30))


def test_bucket_weekly_sums_bounds_linearly():
    buckets = bucket_weekly(_daily(14))
    assert buckets[0].lower == 14.0  # 7 * (3 - 1)
    assert buckets[0].upper == 28.0  # 7 * (3 + 1)


def test_bucket_weekly_without_bounds_keeps_none():
    buckets = bucket_weekly(_daily(10, with_bounds=False))
    assert len(buckets) == 2
    assert buckets[0].lower is None and buckets[0].upper is None
    assert buckets[1].yhat == 9.0  # 3-day partial bucket


def test_bucket_weekly_empty():
    assert bucket_weekly([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py -v`
Expected: FAIL with `ImportError: cannot import name 'bucket_weekly'`

- [ ] **Step 3: Implement `bucket_weekly`**

Append to `backend/app/services/demand_forecasting.py` (after the `DemandForecastResult` dataclass):

```python
def bucket_weekly(points: list[ForecastPoint], bucket_days: int = 7) -> list[ForecastPoint]:
    """Aggregate consecutive daily points into bucket_days-wide buckets.

    Bucket quantity is the sum of the daily yhat values; the last bucket may
    cover fewer than bucket_days days and sums only what it has. Bounds sum
    linearly because the forecast is a flat level: a level error repeats
    identically every day, so daily errors are fully correlated and interval
    widths add. If any day in a bucket lacks bounds the bucket has none.
    """
    buckets: list[ForecastPoint] = []
    for i in range(0, len(points), bucket_days):
        chunk = points[i : i + bucket_days]
        has_bounds = all(p.lower is not None and p.upper is not None for p in chunk)
        buckets.append(
            ForecastPoint(
                period_start=chunk[0].period_start,
                yhat=sum(p.yhat for p in chunk),
                lower=sum(p.lower for p in chunk) if has_bounds else None,
                upper=sum(p.upper for p in chunk) if has_bounds else None,
            )
        )
    return buckets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/demand_forecasting.py backend/tests/test_forecast_suggestions.py
git commit -m "feat: weekly bucketing for demand forecast points"
```

---

### Task 2: Bulk suggestion helper and GET /forecasts/suggested

**Files:**
- Modify: `backend/app/services/demand_forecasting.py`
- Modify: `backend/app/api/routes/forecasts.py`
- Test: `backend/tests/test_forecast_suggestions.py` (append)

**Interfaces:**
- Consumes: `bucket_weekly` from Task 1; `daily_consumption_by_item(db, org_id, since, *, item_ids=None)` from `app/services/demand_history.py`; existing `SuggestedForecastResponse` / `SuggestedForecastPoint` schemas in `forecasts.py`.
- Produces:
  - `ItemWeeklySuggestion` dataclass: `item_id: uuid.UUID`, `item_sku: str`, `item_name: str`, `method_used: str`, `history_points: int`, `points: list[ForecastPoint]` (weekly buckets).
  - `async compute_weekly_suggestions(db, org_id, *, horizon_days=DEFAULT_HORIZON_DAYS, lookback_days=DEFAULT_LOOKBACK_DAYS, as_of: date | None = None, item_ids: set[uuid.UUID] | None = None) -> list[ItemWeeklySuggestion]` (sorted by sku, excludes no-history items). Used by Task 3.
  - `GET /forecasts/suggested?horizon_days=` returning `BulkSuggestedResponse { window_start: date, window_end: date, items: list[SuggestedForecastResponse] }`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_forecast_suggestions.py`. These are API tests and need the app fixtures plus history seeding, so first extend the file header imports:

```python
import uuid as uuid_module
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _insert_consumption(
    test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty_by_day_offset: dict
):
    """Backdated InventoryTransaction consumption rows - the API cannot backdate
    created_at, so history has to be seeded directly for a forecast test."""
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
```

(`date`, `timedelta`, `ForecastPoint`, `bucket_weekly` are already imported by Task 1's header; merge the import blocks.)

Then the tests:

```python
async def test_bulk_suggested_empty_org(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.get("/forecasts/suggested", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    ws = date.fromisoformat(body["window_start"])
    we = date.fromisoformat(body["window_end"])
    assert (we - ws).days == 30 - 1  # default horizon, window inclusive of both ends


async def test_bulk_suggested_buckets_and_excludes_no_history(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    with_history = await _create_item(client, token, sku="RAW-1")
    await _create_item(client, token, sku="RAW-2")  # no history, must not appear
    location_id = await _default_location_id(client, token)

    # 3 non-zero days in the last 7: moving average of (7+7+7+0+0+0+0)/7 == 3/day
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=with_history["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={0: 7, 1: 7, 2: 7},
    )

    res = await client.get("/forecasts/suggested?horizon_days=10", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["item_sku"] == "RAW-1"
    assert item["method_used"] == "moving_avg"
    assert item["history_points"] == 3
    # 10 daily points -> one 7-day bucket then one 3-day bucket
    assert len(item["points"]) == 2
    assert Decimal(str(item["points"][0]["quantity"])) == Decimal("21")
    assert Decimal(str(item["points"][1]["quantity"])) == Decimal("9")
    assert item["points"][0]["period_start"] == body["window_start"]
    assert (
        date.fromisoformat(item["points"][1]["period_start"])
        - date.fromisoformat(item["points"][0]["period_start"])
    ).days == 7

    # org isolation: a second org sees none of this history
    reg2, token2 = await _register_and_login(client, email="owner2@example.com", org_name="Beta")
    other = await client.get("/forecasts/suggested", headers={"Authorization": f"Bearer {token2}"})
    assert other.status_code == 200
    assert other.json()["items"] == []


async def test_bulk_suggested_validates_horizon(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/forecasts/suggested?horizon_days=0", headers=headers)).status_code == 400
    assert (await client.get("/forecasts/suggested?horizon_days=366", headers=headers)).status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py -v`
Expected: Task 1 tests pass, the three new tests FAIL with 404 (route does not exist)

- [ ] **Step 3: Refactor the per-item forecast core so bulk can reuse it**

In `backend/app/services/demand_forecasting.py`, extract the body of `compute_demand_forecast` after the history fetch into a private function, and add the bulk helper. Final shape of the affected code:

```python
def _forecast_from_daily(
    item_id: uuid.UUID,
    daily: dict[date, Decimal],
    *,
    lookback_days: int,
    horizon_days: int,
    as_of: date,
) -> DemandForecastResult:
    """The per-item math shared by compute_demand_forecast (one item, its own
    history query) and compute_weekly_suggestions (all items, one bulk query)."""
    values = _series_for_item(daily, lookback_days, as_of)
    non_zero_days = sum(1 for v in values if v > 0)

    if non_zero_days == 0:
        return DemandForecastResult(item_id=item_id, method_used="no_data", history_points=0, points=[])

    method = choose_forecast_method(non_zero_days)
    if method == "exp_smoothing":
        alpha = (
            EXP_SMOOTHING_ALPHA_LONG_HISTORY
            if non_zero_days >= EXP_SMOOTHING_LONG_HISTORY_THRESHOLD_DAYS
            else EXP_SMOOTHING_ALPHA_SHORT_HISTORY
        )
        fc_values, resid_std = exp_smoothing_forecast(values, horizon_days, alpha)
    else:
        alpha = 0.0
        fc_values = moving_average_forecast(values, horizon_days, MOVING_AVERAGE_WINDOW_DAYS)
        resid_std = 0.0

    points: list[ForecastPoint] = []
    for i, yhat in enumerate(fc_values, start=1):
        lower = upper = None
        if resid_std > 0:
            # Widens with horizon: the h-step forecast std for ETS(A,N,N)
            # grows as sigma * sqrt(1 + (h-1)*alpha^2); a constant band would
            # understate risk further out.
            step_std = resid_std * math.sqrt(1.0 + (i - 1) * (alpha**2))
            lower = max(0.0, yhat - Z_95 * step_std)
            upper = yhat + Z_95 * step_std
        points.append(
            ForecastPoint(period_start=as_of + timedelta(days=i), yhat=max(0.0, yhat), lower=lower, upper=upper)
        )

    return DemandForecastResult(item_id=item_id, method_used=method, history_points=non_zero_days, points=points)


async def compute_demand_forecast(
    db: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> DemandForecastResult:
    """Data-driven demand projection for one item, for the caller to review
    before turning it into an actual DemandForecast (upsert_forecast) -
    read-only and side-effect-free, same boundary as inventory analytics."""
    as_of = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    daily = (await daily_consumption_by_item(db, org_id, since, item_ids={item_id})).get(item_id, {})
    return _forecast_from_daily(
        item_id, daily, lookback_days=lookback_days, horizon_days=horizon_days, as_of=as_of
    )
```

`compute_demand_forecast` behavior is unchanged; the existing tests in `tests/test_demand_forecasting.py` must still pass untouched.

Then add, at the end of the file:

```python
@dataclass
class ItemWeeklySuggestion:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    method_used: str
    history_points: int
    points: list[ForecastPoint]  # weekly buckets, not days


async def compute_weekly_suggestions(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    as_of: date | None = None,
    item_ids: set[uuid.UUID] | None = None,
) -> list[ItemWeeklySuggestion]:
    """Weekly-bucketed suggestions for every item with consumption history
    (or the given subset). One bulk history query, then the same per-item math
    as compute_demand_forecast, then bucket_weekly. Items whose history is
    empty in the lookback window are omitted entirely: no history means no
    suggestion, not a zero suggestion.

    as_of is injectable so a route can compute one date and share it between
    this projection and its own delete window.
    """
    # ponytail: in-request loop over items; move to a background job if org
    # catalogs reach tens of thousands of items
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    daily_all = await daily_consumption_by_item(db, org_id, since, item_ids=item_ids)
    if not daily_all:
        return []

    from sqlalchemy import select

    from app.models.bom import Item

    items = {
        item.id: item
        for item in (
            await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(daily_all.keys())))
        )
        .scalars()
        .all()
    }

    suggestions: list[ItemWeeklySuggestion] = []
    for iid, daily in daily_all.items():
        item = items.get(iid)
        if item is None:
            continue
        result = _forecast_from_daily(
            iid, daily, lookback_days=lookback_days, horizon_days=horizon_days, as_of=as_of
        )
        if result.method_used == "no_data":
            continue
        suggestions.append(
            ItemWeeklySuggestion(
                item_id=iid,
                item_sku=item.sku,
                item_name=item.name,
                method_used=result.method_used,
                history_points=result.history_points,
                points=bucket_weekly(result.points),
            )
        )
    suggestions.sort(key=lambda s: s.item_sku)
    return suggestions
```

Note the local `select` / `Item` imports keep the service free of a module-level model dependency it did not have; if the file already imports these at module level after rebase, use those instead.

- [ ] **Step 4: Add the GET route**

In `backend/app/api/routes/forecasts.py`:

Add to the imports from `app.services.demand_forecasting`:

```python
from app.services.demand_forecasting import (
    DEFAULT_HORIZON_DAYS,
    compute_demand_forecast,
    compute_weekly_suggestions,
)
```

Add the schema next to `SuggestedForecastResponse`:

```python
class BulkSuggestedResponse(BaseModel):
    # Suggestion window, inclusive both ends: [as_of + 1, as_of + horizon_days].
    # The accept route deletes existing rows in exactly this window, so the
    # client can label "replaces N rows" from the same dates it displays.
    window_start: date
    window_end: date
    # Same per-item shape as the single-item route, but points are weekly
    # buckets (see services.demand_forecasting.bucket_weekly), not days.
    items: list[SuggestedForecastResponse]
```

Add a small converter next to it (the per-item route builds the same shape inline; reuse for both is a follow-up, keep the existing route untouched):

```python
def _suggestion_to_response(s) -> SuggestedForecastResponse:
    return SuggestedForecastResponse(
        item_id=str(s.item_id),
        item_sku=s.item_sku,
        item_name=s.item_name,
        method_used=s.method_used,
        history_points=s.history_points,
        points=[
            SuggestedForecastPoint(
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                lower=Decimal(str(round(p.lower, 4))) if p.lower is not None else None,
                upper=Decimal(str(round(p.upper, 4))) if p.upper is not None else None,
            )
            for p in s.points
        ],
    )
```

Add the route directly above the existing `get_suggested_forecast`:

```python
@router.get("/suggested", response_model=BulkSuggestedResponse)
async def list_suggested_forecasts(
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Weekly-bucketed, read-only demand suggestions for every item with
    consumption history, for review before POST /forecasts/suggested/accept."""
    if horizon_days <= 0 or horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")
    as_of = datetime.now(timezone.utc).date()
    suggestions = await compute_weekly_suggestions(
        db, current.org_id, horizon_days=horizon_days, as_of=as_of
    )
    return BulkSuggestedResponse(
        window_start=as_of + timedelta(days=1),
        window_end=as_of + timedelta(days=horizon_days),
        items=[_suggestion_to_response(s) for s in suggestions],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py tests/test_demand_forecasting.py -v`
Expected: all pass (including the untouched per-item suite, proving the refactor changed nothing)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/demand_forecasting.py backend/app/api/routes/forecasts.py backend/tests/test_forecast_suggestions.py
git commit -m "feat: bulk weekly forecast suggestions endpoint"
```

---

### Task 3: POST /forecasts/suggested/accept

**Files:**
- Modify: `backend/app/api/routes/forecasts.py`
- Test: `backend/tests/test_forecast_suggestions.py` (append)

**Interfaces:**
- Consumes: `compute_weekly_suggestions` (Task 2), `_upsert_forecast` and `_parse_item_id` (existing in `forecasts.py`), `log_event`, `require_role`.
- Produces: `POST /forecasts/suggested/accept` with body `{ "item_ids": [str], "horizon_days": int }` returning `{ "items": int, "deleted": int, "created": int, "updated": int, "skipped": [{"item_id", "item_sku"}] }`. The frontend (Task 6) relies on these exact field names.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_forecast_suggestions.py`:

```python
async def _seed_three_day_history(client, test_engine, reg, token, sku="RAW-1"):
    """Item with 3 non-zero days: moving average of 3/day. With
    horizon_days=10 the weekly suggestion is [21, 9]."""
    item = await _create_item(client, token, sku=sku)
    location_id = await _default_location_id(client, token)
    await _insert_consumption(
        test_engine,
        org_id=reg["org_id"],
        item_id=item["id"],
        location_id=location_id,
        created_by=reg["user_id"],
        qty_by_day_offset={0: 7, 1: 7, 2: 7},
    )
    return item


async def test_accept_writes_suggested_rows(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _seed_three_day_history(client, test_engine, reg, token)

    res = await client.post(
        "/forecasts/suggested/accept",
        json={"item_ids": [item["id"]], "horizon_days": 10},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": 1, "deleted": 0, "created": 2, "updated": 0, "skipped": []}

    rows = (await client.get(f"/forecasts?item_id={item['id']}", headers=headers)).json()
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"suggested"}
    assert {r["notes"] for r in rows} == {"from history (moving_avg, 3 days)"}
    assert sorted(Decimal(str(r["quantity"])) for r in rows) == [Decimal("9"), Decimal("21")]


async def test_accept_replaces_in_window_rows_only(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _seed_three_day_history(client, test_engine, reg, token)

    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    in_window = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": tomorrow, "quantity": "999"},
        headers=headers,
    )
    assert in_window.status_code == 200
    outside = await client.post(
        "/forecasts",
        json={"item_id": item["id"], "period_start": "2035-01-01", "quantity": "555"},
        headers=headers,
    )
    assert outside.status_code == 200

    res = await client.post(
        "/forecasts/suggested/accept",
        json={"item_ids": [item["id"]], "horizon_days": 10},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == 1  # the manual row at tomorrow, inside the window
    assert body["created"] == 2

    rows = (await client.get(f"/forecasts?item_id={item['id']}", headers=headers)).json()
    by_period = {r["period_start"]: r for r in rows}
    assert "2035-01-01" in by_period  # out-of-window manual row untouched
    assert by_period["2035-01-01"]["source"] == "manual"
    assert len(rows) == 3  # 2 suggested + 1 far-future manual


async def test_accept_rerun_same_day_is_stable(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _seed_three_day_history(client, test_engine, reg, token)
    payload = {"item_ids": [item["id"]], "horizon_days": 10}

    first = (await client.post("/forecasts/suggested/accept", json=payload, headers=headers)).json()
    assert first["created"] == 2
    second = (await client.post("/forecasts/suggested/accept", json=payload, headers=headers)).json()
    # Same as_of: the rerun replaces its own rows, never accumulates them.
    assert second["deleted"] == 2
    assert second["created"] + second["updated"] == 2
    rows = (await client.get(f"/forecasts?item_id={item['id']}", headers=headers)).json()
    assert len(rows) == 2


async def test_accept_skips_no_history_item_without_deleting(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    bare = await _create_item(client, token, sku="RAW-9")

    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    keep = await client.post(
        "/forecasts",
        json={"item_id": bare["id"], "period_start": tomorrow, "quantity": "42"},
        headers=headers,
    )
    assert keep.status_code == 200

    res = await client.post(
        "/forecasts/suggested/accept",
        json={"item_ids": [bare["id"]], "horizon_days": 10},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == 0 and body["deleted"] == 0 and body["created"] == 0
    assert body["skipped"] == [{"item_id": bare["id"], "item_sku": "RAW-9"}]

    rows = (await client.get(f"/forecasts?item_id={bare['id']}", headers=headers)).json()
    assert len(rows) == 1  # the manual row survived


async def test_accept_validation_and_permissions(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = reg["org_id"]

    # empty item_ids
    assert (
        await client.post(
            "/forecasts/suggested/accept", json={"item_ids": []}, headers=headers
        )
    ).status_code == 400
    # bad horizon
    item = await _create_item(client, token, sku="RAW-5")
    assert (
        await client.post(
            "/forecasts/suggested/accept",
            json={"item_ids": [item["id"]], "horizon_days": 0},
            headers=headers,
        )
    ).status_code == 400
    # unknown item for this org
    assert (
        await client.post(
            "/forecasts/suggested/accept",
            json={"item_ids": [str(uuid_module.uuid4())]},
            headers=headers,
        )
    ).status_code == 404

    # writes are owner-only
    from app.core.security import hash_password
    from app.models.user import User

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (
        await client.post(
            "/forecasts/suggested/accept",
            json={"item_ids": [item["id"]]},
            headers=member_headers,
        )
    ).status_code == 403
    # but the review payload is readable by any member
    assert (await client.get("/forecasts/suggested", headers=member_headers)).status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py -v`
Expected: the new `test_accept_*` tests FAIL with 404 (route does not exist); everything else passes

- [ ] **Step 3: Implement the accept route**

In `backend/app/api/routes/forecasts.py`:

Add `delete` to the SQLAlchemy import:

```python
from sqlalchemy import delete, func, select, text
```

Add schemas next to `BulkSuggestedResponse`:

```python
class AcceptSuggestedRequest(BaseModel):
    item_ids: list[str]
    horizon_days: int = DEFAULT_HORIZON_DAYS


class SkippedSuggestion(BaseModel):
    item_id: str
    item_sku: str


class AcceptSuggestedResponse(BaseModel):
    items: int  # items actually written
    deleted: int
    created: int
    updated: int
    # Requested items that had no consumption history at accept time; their
    # existing forecast rows are left untouched.
    skipped: list[SkippedSuggestion]
```

Add the route below `list_suggested_forecasts`:

```python
MAX_ACCEPT_ITEMS = 500


@router.post("/suggested/accept", response_model=AcceptSuggestedResponse)
async def accept_suggested_forecasts(
    body: AcceptSuggestedRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Turn weekly suggestions into DemandForecast rows, server-authoritative:
    quantities are recomputed here, never taken from the client. For each item
    every existing row inside the suggestion window is deleted first. That is
    mandatory, not a convenience: as_of moves daily, so bucket boundaries
    shift, and plain upsert would leave yesterday's rows behind at the old
    boundaries, silently double-counting demand in MRP. Whole request is
    atomic, same as /forecasts/import."""
    if not body.item_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No item_ids provided")
    if len(body.item_ids) > MAX_ACCEPT_ITEMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"At most {MAX_ACCEPT_ITEMS} items per accept"
        )
    if body.horizon_days <= 0 or body.horizon_days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "horizon_days must be between 1 and 365")

    parsed = list(dict.fromkeys(_parse_item_id(raw) for raw in body.item_ids))

    sku_by_id = {
        row.id: row.sku
        for row in (
            await db.execute(
                select(Item.id, Item.sku).where(Item.org_id == current.org_id, Item.id.in_(parsed))
            )
        ).all()
    }
    missing = set(parsed) - set(sku_by_id)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}",
        )

    as_of = datetime.now(timezone.utc).date()
    window_start = as_of + timedelta(days=1)
    window_end = as_of + timedelta(days=body.horizon_days)
    suggestions = {
        s.item_id: s
        for s in await compute_weekly_suggestions(
            db, current.org_id, horizon_days=body.horizon_days, as_of=as_of, item_ids=set(parsed)
        )
    }

    written = deleted = created = updated = 0
    skipped: list[SkippedSuggestion] = []
    for iid in parsed:
        sugg = suggestions.get(iid)
        if sugg is None:
            skipped.append(SkippedSuggestion(item_id=str(iid), item_sku=sku_by_id[iid]))
            continue
        result = await db.execute(
            delete(DemandForecast).where(
                DemandForecast.org_id == current.org_id,
                DemandForecast.item_id == iid,
                DemandForecast.period_start >= window_start,
                DemandForecast.period_start <= window_end,
            )
        )
        deleted += result.rowcount
        notes = f"from history ({sugg.method_used}, {sugg.history_points} days)"
        for p in sugg.points:
            _, was_created = await _upsert_forecast(
                db,
                org_id=current.org_id,
                item_id=iid,
                period_start=p.period_start,
                quantity=Decimal(str(round(p.yhat, 4))),
                notes=notes,
                source="suggested",
                created_by=current.id,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        written += 1

    await log_event(
        db,
        action="forecast.suggested_accepted",
        org_id=current.org_id,
        actor_user_id=current.id,
        metadata={
            "items": written,
            "deleted": deleted,
            "created": created,
            "updated": updated,
            "skipped": len(skipped),
            "horizon_days": body.horizon_days,
        },
    )
    await db.commit()
    return AcceptSuggestedResponse(
        items=written, deleted=deleted, created=created, updated=updated, skipped=skipped
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_forecast_suggestions.py tests/test_forecast.py tests/test_demand_forecasting.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/forecasts.py backend/tests/test_forecast_suggestions.py
git commit -m "feat: accept endpoint turning weekly suggestions into forecasts"
```

---

### Task 4: CSV parser (frontend pure module)

**Files:**
- Create: `frontend/src/lib/csv.ts`
- Test: `frontend/src/lib/csv.test.ts`

**Interfaces:**
- Produces: `parseCsv(text: string): string[][]` handling double-quoted fields (embedded commas, embedded `""` escapes), `\n` and `\r\n` newlines, and skipping blank lines. Used by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/csv.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseCsv } from "./csv";

describe("parseCsv", () => {
  it("parses simple rows", () => {
    expect(parseCsv("a,b,c\n1,2,3")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("handles quoted fields with commas", () => {
    expect(parseCsv('sku,"name, long",qty')).toEqual([["sku", "name, long", "qty"]]);
  });

  it("handles escaped quotes inside quoted fields", () => {
    expect(parseCsv('a,"say ""hi""",c')).toEqual([["a", 'say "hi"', "c"]]);
  });

  it("handles CRLF and trailing newline", () => {
    expect(parseCsv("a,b\r\n1,2\r\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("skips blank lines", () => {
    expect(parseCsv("a,b\n\n1,2\n\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("returns empty for empty input", () => {
    expect(parseCsv("")).toEqual([]);
    expect(parseCsv("\n\n")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/csv.test.ts`
Expected: FAIL, cannot resolve `./csv`

- [ ] **Step 3: Implement the parser**

Create `frontend/src/lib/csv.ts`:

```ts
// Minimal quote-aware CSV parser: double-quoted fields may contain commas,
// newlines are row separators (\n or \r\n), "" inside a quoted field is an
// escaped quote. Enough for forecast imports; not a general RFC 4180 reader.
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;
  let sawAny = false;

  const pushField = () => {
    row.push(field);
    field = "";
  };
  const pushRow = () => {
    pushField();
    if (row.length > 1 || row[0] !== "") rows.push(row);
    row = [];
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      sawAny = true;
    } else if (ch === ",") {
      pushField();
      sawAny = true;
    } else if (ch === "\n") {
      pushRow();
    } else if (ch !== "\r") {
      field += ch;
      sawAny = true;
    }
  }
  if (sawAny && (field !== "" || row.length > 0)) pushRow();
  return rows;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/csv.test.ts`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/csv.ts frontend/src/lib/csv.test.ts
git commit -m "feat: quote-aware csv parser for forecast import"
```

---

### Task 5: Suggestion conflict logic and CSV row mapping (frontend pure modules)

**Files:**
- Create: `frontend/src/pages/planning/suggestLogic.ts`
- Create: `frontend/src/pages/planning/suggestLogic.test.ts`
- Create: `frontend/src/pages/planning/csvImport.ts`
- Create: `frontend/src/pages/planning/csvImport.test.ts`

**Interfaces:**
- Consumes: nothing from other tasks (pure modules; `csvImport.ts` receives already-parsed `string[][]`).
- Produces (used by Tasks 6 and 7):

```ts
// suggestLogic.ts
export interface ExistingForecastLite { item_id: string; period_start: string; source: string }
export interface ConflictSummary { total: number; manual: number; defaultChecked: boolean }
export function summarizeConflicts(
  itemId: string, windowStart: string, windowEnd: string, existing: ExistingForecastLite[],
): ConflictSummary

// csvImport.ts
export interface CsvForecastRow { item_id: string; period_start: string; quantity: string; notes: string | null }
export interface CsvRowError { line: number; message: string }
export const CSV_TEMPLATE: string
export function mapForecastCsv(
  records: string[][], itemIdBySku: Map<string, string>,
): { rows: CsvForecastRow[]; errors: CsvRowError[] }
```

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/planning/suggestLogic.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { summarizeConflicts } from "./suggestLogic";

const existing = [
  { item_id: "A", period_start: "2026-08-01", source: "manual" },
  { item_id: "A", period_start: "2026-08-05", source: "suggested" },
  { item_id: "A", period_start: "2036-01-01", source: "manual" }, // outside window
  { item_id: "B", period_start: "2026-08-02", source: "manual" }, // other item
];

describe("summarizeConflicts", () => {
  it("counts in-window rows for the item and unchecks by default", () => {
    const s = summarizeConflicts("A", "2026-07-26", "2026-08-24", existing);
    expect(s).toEqual({ total: 2, manual: 1, defaultChecked: false });
  });

  it("checks by default when the item has no in-window rows", () => {
    const s = summarizeConflicts("C", "2026-07-26", "2026-08-24", existing);
    expect(s).toEqual({ total: 0, manual: 0, defaultChecked: true });
  });

  it("treats window bounds as inclusive", () => {
    const s = summarizeConflicts(
      "A",
      "2026-08-01",
      "2026-08-05",
      existing,
    );
    expect(s.total).toBe(2);
  });
});
```

Create `frontend/src/pages/planning/csvImport.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { mapForecastCsv } from "./csvImport";

const items = new Map([
  ["RAW-1", "id-1"],
  ["RAW-2", "id-2"],
]);

describe("mapForecastCsv", () => {
  it("maps valid rows, sku to item_id", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity", "notes"],
        ["RAW-1", "2026-08-01", "100", "week 1"],
        ["RAW-2", "2026-08-01", "2.5", ""],
      ],
      items,
    );
    expect(errors).toEqual([]);
    expect(rows).toEqual([
      { item_id: "id-1", period_start: "2026-08-01", quantity: "100", notes: "week 1" },
      { item_id: "id-2", period_start: "2026-08-01", quantity: "2.5", notes: null },
    ]);
  });

  it("accepts a header without the optional notes column", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity"],
        ["RAW-1", "2026-08-01", "1"],
      ],
      items,
    );
    expect(errors).toEqual([]);
    expect(rows[0].notes).toBeNull();
  });

  it("rejects a bad header", () => {
    const { errors } = mapForecastCsv([["sku", "qty"]], items);
    expect(errors[0].line).toBe(1);
    expect(errors[0].message).toMatch(/header/i);
  });

  it("flags unknown sku, bad date, bad quantity with line numbers", () => {
    const { rows, errors } = mapForecastCsv(
      [
        ["sku", "period_start", "quantity"],
        ["NOPE", "2026-08-01", "1"],
        ["RAW-1", "08/01/2026", "1"],
        ["RAW-1", "2026-08-01", "-5"],
        ["RAW-1", "2026-08-01", "abc"],
      ],
      items,
    );
    expect(rows).toEqual([]);
    expect(errors.map((e) => e.line)).toEqual([2, 3, 4, 5]);
  });

  it("errors on empty input", () => {
    const { errors } = mapForecastCsv([], items);
    expect(errors).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/planning`
Expected: FAIL, cannot resolve `./suggestLogic` and `./csvImport`

- [ ] **Step 3: Implement both modules**

Create `frontend/src/pages/planning/suggestLogic.ts`:

```ts
export interface ExistingForecastLite {
  item_id: string;
  period_start: string; // YYYY-MM-DD
  source: string;
}

export interface ConflictSummary {
  total: number;
  manual: number;
  defaultChecked: boolean;
}

// ISO date strings compare correctly as strings, so the window check is
// plain lexical comparison, inclusive on both ends to match the accept
// route's delete window.
export function summarizeConflicts(
  itemId: string,
  windowStart: string,
  windowEnd: string,
  existing: ExistingForecastLite[],
): ConflictSummary {
  let total = 0;
  let manual = 0;
  for (const fc of existing) {
    if (fc.item_id !== itemId) continue;
    if (fc.period_start < windowStart || fc.period_start > windowEnd) continue;
    total++;
    if (fc.source === "manual") manual++;
  }
  return { total, manual, defaultChecked: total === 0 };
}
```

Create `frontend/src/pages/planning/csvImport.ts`:

```ts
export interface CsvForecastRow {
  item_id: string;
  period_start: string;
  quantity: string;
  notes: string | null;
}

export interface CsvRowError {
  line: number; // 1-based line in the file, header is line 1
  message: string;
}

export const CSV_TEMPLATE = 'sku,period_start,quantity,notes\nRAW-1,2026-08-01,100,"optional note"\n';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function mapForecastCsv(
  records: string[][],
  itemIdBySku: Map<string, string>,
): { rows: CsvForecastRow[]; errors: CsvRowError[] } {
  const errors: CsvRowError[] = [];
  if (records.length === 0) {
    return { rows: [], errors: [{ line: 1, message: "File is empty" }] };
  }
  const header = records[0].map((h) => h.trim().toLowerCase());
  const okHeader =
    (header.length === 3 || (header.length === 4 && header[3] === "notes")) &&
    header[0] === "sku" &&
    header[1] === "period_start" &&
    header[2] === "quantity";
  if (!okHeader) {
    return {
      rows: [],
      errors: [{ line: 1, message: "Header must be: sku,period_start,quantity[,notes]" }],
    };
  }

  const rows: CsvForecastRow[] = [];
  for (let i = 1; i < records.length; i++) {
    const line = i + 1;
    const [skuRaw = "", dateRaw = "", qtyRaw = "", notesRaw = ""] = records[i];
    const sku = skuRaw.trim();
    const item_id = itemIdBySku.get(sku);
    if (!item_id) {
      errors.push({ line, message: `Unknown SKU: ${sku || "(empty)"}` });
      continue;
    }
    const period_start = dateRaw.trim();
    if (!DATE_RE.test(period_start) || Number.isNaN(Date.parse(period_start))) {
      errors.push({ line, message: `Bad date (expected YYYY-MM-DD): ${period_start}` });
      continue;
    }
    const quantity = qtyRaw.trim();
    const qty = Number(quantity);
    if (quantity === "" || !Number.isFinite(qty) || qty < 0) {
      errors.push({ line, message: `Quantity must be a non-negative number: ${quantity}` });
      continue;
    }
    const notes = notesRaw.trim();
    if (notes.length > 255) {
      errors.push({ line, message: "Notes longer than 255 characters" });
      continue;
    }
    rows.push({ item_id, period_start, quantity, notes: notes === "" ? null : notes });
  }
  return { rows, errors };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/planning src/lib/csv.test.ts`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/planning/suggestLogic.ts frontend/src/pages/planning/suggestLogic.test.ts frontend/src/pages/planning/csvImport.ts frontend/src/pages/planning/csvImport.test.ts
git commit -m "feat: conflict-default and csv mapping logic for planning"
```

---

### Task 6: Suggest-from-history card

**Files:**
- Create: `frontend/src/pages/planning/SuggestFromHistory.tsx`
- Modify: `frontend/src/pages/Planning.tsx`

**Interfaces:**
- Consumes: `summarizeConflicts`, `ExistingForecastLite` (Task 5); `GET /forecasts/suggested` and `POST /forecasts/suggested/accept` (Tasks 2 and 3, exact field names as defined there); existing `apiFetch`, UI primitives, `fmt` from `./analytics/shared`.
- Produces: `SuggestFromHistoryCard()` React component rendered by `Planning`.

- [ ] **Step 1: Implement the component**

Create `frontend/src/pages/planning/SuggestFromHistory.tsx`:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { fmt } from "../analytics/shared";
import { summarizeConflicts, type ExistingForecastLite } from "./suggestLogic";

interface SuggestedPoint {
  period_start: string;
  quantity: string;
  lower: string | null;
  upper: string | null;
}

interface SuggestedItem {
  item_id: string;
  item_sku: string;
  item_name: string;
  method_used: string;
  history_points: number;
  points: SuggestedPoint[];
}

interface BulkSuggestedResponse {
  window_start: string;
  window_end: string;
  items: SuggestedItem[];
}

interface AcceptResponse {
  items: number;
  deleted: number;
  created: number;
  updated: number;
  skipped: { item_id: string; item_sku: string }[];
}

const METHOD_LABEL: Record<string, string> = {
  moving_avg: "moving average",
  exp_smoothing: "exp. smoothing",
};

export function SuggestFromHistoryCard() {
  const queryClient = useQueryClient();
  const [horizonDays, setHorizonDays] = useState("30");
  const [error, setError] = useState<string | null>(null);
  // Checkbox overrides on top of the conflict-derived defaults; reset per compute.
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<AcceptResponse | null>(null);

  const forecasts = useQuery<ExistingForecastLite[]>({
    queryKey: ["forecasts"],
    queryFn: async () => {
      const res = await apiFetch("/forecasts");
      if (!res.ok) throw new Error("Failed to load forecasts");
      return res.json();
    },
  });

  const suggestions = useQuery<BulkSuggestedResponse>({
    queryKey: ["suggested-forecasts", horizonDays],
    enabled: false, // O(items) computation; the planner triggers it deliberately
    queryFn: async () => {
      const res = await apiFetch(`/forecasts/suggested?horizon_days=${horizonDays}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to compute suggestions");
      }
      return res.json();
    },
  });

  function onCompute() {
    setError(null);
    setResult(null);
    setOverrides({});
    void suggestions.refetch();
  }

  const data = suggestions.data;
  const existing = forecasts.data ?? [];

  function isChecked(item: SuggestedItem): boolean {
    if (item.item_id in overrides) return overrides[item.item_id];
    if (!data) return false;
    return summarizeConflicts(item.item_id, data.window_start, data.window_end, existing)
      .defaultChecked;
  }

  const checkedIds = (data?.items ?? []).filter(isChecked).map((s) => s.item_id);

  const accept = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/forecasts/suggested/accept", {
        method: "POST",
        body: JSON.stringify({ item_ids: checkedIds, horizon_days: Number(horizonDays) }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to accept suggestions");
      }
      return (await res.json()) as AcceptResponse;
    },
    onSuccess: (r) => {
      setResult(r);
      setOverrides({});
      queryClient.invalidateQueries({ queryKey: ["forecasts"] });
      queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
      void suggestions.refetch();
    },
    onError: (e: Error) => setError(e.message),
  });

  function onAccept() {
    setError(null);
    setResult(null);
    if (checkedIds.length === 0) {
      setError("Select at least one item to accept");
      return;
    }
    accept.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Suggest from history</CardTitle>
        <CardDescription>
          Project demand from each item's real consumption history, then accept the projections you
          agree with as forecasts. Accepting an item replaces its existing forecast rows inside the
          suggestion window so demand is never double counted.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {error && <Alert variant="destructive">{error}</Alert>}

        <div className="grid gap-4 sm:grid-cols-[10rem_auto] sm:items-end">
          <Field label="Horizon (days)" htmlFor="sg-horizon">
            <Input
              id="sg-horizon"
              type="number"
              min="1"
              max="365"
              value={horizonDays}
              onChange={(e) => setHorizonDays(e.target.value)}
            />
          </Field>
          <div>
            <Button onClick={onCompute} loading={suggestions.isFetching}>
              Compute suggestions
            </Button>
          </div>
        </div>

        {suggestions.isError && (
          <Alert variant="destructive">{(suggestions.error as Error).message}</Alert>
        )}

        {result && (
          <Alert variant="success">
            Accepted {result.items} item{result.items === 1 ? "" : "s"}: {result.created} created,{" "}
            {result.updated} updated, {result.deleted} replaced.
            {result.skipped.length > 0 &&
              ` Skipped (no history): ${result.skipped.map((s) => s.item_sku).join(", ")}.`}
          </Alert>
        )}

        {data && data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">No items have consumption history yet.</p>
        )}

        {data && data.items.length > 0 && (
          <>
            <p className="text-sm text-muted-foreground">
              Window {data.window_start} to {data.window_end}. Weekly quantities; range shown when
              history supports one.
            </p>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Accept?</TH>
                    <TH>Item</TH>
                    <TH>Method</TH>
                    <TH>History</TH>
                    <TH>Week starting</TH>
                    <TH>Quantity</TH>
                    <TH>Range</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.items.map((s) => {
                    const conflict = summarizeConflicts(
                      s.item_id,
                      data.window_start,
                      data.window_end,
                      existing,
                    );
                    return s.points.map((p, idx) => (
                      <TR key={`${s.item_id}-${p.period_start}`}>
                        {idx === 0 && (
                          <>
                            <TD rowSpan={s.points.length}>
                              <Checkbox
                                checked={isChecked(s)}
                                onChange={(e) =>
                                  setOverrides((prev) => ({
                                    ...prev,
                                    [s.item_id]: e.target.checked,
                                  }))
                                }
                              />
                            </TD>
                            <TD rowSpan={s.points.length} className="font-medium">
                              {s.item_sku} - {s.item_name}
                              {conflict.total > 0 && (
                                <div className="mt-1 text-xs font-normal text-muted-foreground">
                                  replaces {conflict.total} existing row
                                  {conflict.total === 1 ? "" : "s"}
                                  {conflict.manual > 0 && ` (${conflict.manual} manual)`}
                                </div>
                              )}
                            </TD>
                            <TD rowSpan={s.points.length}>
                              <Badge variant="neutral">
                                {METHOD_LABEL[s.method_used] ?? s.method_used}
                              </Badge>
                            </TD>
                            <TD rowSpan={s.points.length}>{s.history_points} days</TD>
                          </>
                        )}
                        <TD>{p.period_start}</TD>
                        <TD className="font-semibold">{fmt(Number(p.quantity))}</TD>
                        <TD className="text-muted-foreground">
                          {p.lower !== null && p.upper !== null
                            ? `${fmt(Number(p.lower))} to ${fmt(Number(p.upper))}`
                            : "—"}
                        </TD>
                      </TR>
                    ));
                  })}
                </TBody>
              </Table>
            </TableWrap>
            <Button onClick={onAccept} loading={accept.isPending}>
              Accept {checkedIds.length} selected item{checkedIds.length === 1 ? "" : "s"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Wire into Planning**

In `frontend/src/pages/Planning.tsx`:

Add the import:

```tsx
import { SuggestFromHistoryCard } from "./planning/SuggestFromHistory";
```

Render it between the demand-forecasts `Card` and the MRP-plan `Card` (after the closing `</Card>` of "Demand forecasts"):

```tsx
      <SuggestFromHistoryCard />
```

- [ ] **Step 3: Verify with the type gate and tests**

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds, all tests pass

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/planning/SuggestFromHistory.tsx frontend/src/pages/Planning.tsx
git commit -m "feat: suggest-from-history card on planning page"
```

---

### Task 7: CSV import card

**Files:**
- Create: `frontend/src/pages/planning/ImportForecastsCsv.tsx`
- Modify: `frontend/src/pages/Planning.tsx`

**Interfaces:**
- Consumes: `parseCsv` (Task 4); `mapForecastCsv`, `CSV_TEMPLATE`, `CsvForecastRow`, `CsvRowError` (Task 5); existing `POST /forecasts/import` (body is a bare JSON array of `{item_id, period_start, quantity, notes}`; response `{created, updated, total}`).
- Produces: `ImportForecastsCsvSection({ items })` component, prop `items: { id: string; sku: string }[]`.

- [ ] **Step 1: Implement the component**

Create `frontend/src/pages/planning/ImportForecastsCsv.tsx`:

```tsx
import { ChangeEvent, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { parseCsv } from "../../lib/csv";
import {
  CSV_TEMPLATE,
  mapForecastCsv,
  type CsvForecastRow,
  type CsvRowError,
} from "./csvImport";

interface ImportResult {
  created: number;
  updated: number;
  total: number;
}

export function ImportForecastsCsvSection({ items }: { items: { id: string; sku: string }[] }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [rows, setRows] = useState<CsvForecastRow[]>([]);
  const [errors, setErrors] = useState<CsvRowError[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    setError(null);
    setResult(null);
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    void file.text().then((text) => {
      const itemIdBySku = new Map(items.map((it) => [it.sku, it.id]));
      const mapped = mapForecastCsv(parseCsv(text), itemIdBySku);
      setRows(mapped.rows);
      setErrors(mapped.errors);
    });
  }

  function onDownloadTemplate() {
    const url = URL.createObjectURL(new Blob([CSV_TEMPLATE], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "forecast-import-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  const importCsv = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/forecasts/import", {
        method: "POST",
        body: JSON.stringify(rows),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Import failed");
      }
      return (await res.json()) as ImportResult;
    },
    onSuccess: (r) => {
      setResult(r);
      setRows([]);
      setErrors([]);
      setFileName(null);
      if (fileRef.current) fileRef.current.value = "";
      queryClient.invalidateQueries({ queryKey: ["forecasts"] });
      queryClient.invalidateQueries({ queryKey: ["mrp-plan"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="space-y-3 border-t border-border pt-6">
      <h3 className="text-sm font-semibold">Import forecasts from CSV</h3>
      <p className="text-sm text-muted-foreground">
        Columns: sku, period_start (YYYY-MM-DD), quantity, notes (optional).{" "}
        <button type="button" onClick={onDownloadTemplate} className="text-primary hover:underline">
          Download template
        </button>
      </p>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        onChange={onFile}
        className="text-sm"
        aria-label="Forecast CSV file"
      />

      {error && <Alert variant="destructive">{error}</Alert>}
      {result && (
        <Alert variant="success">
          Imported {result.total} row{result.total === 1 ? "" : "s"}: {result.created} created,{" "}
          {result.updated} updated.
        </Alert>
      )}

      {errors.length > 0 && (
        <Alert variant="destructive">
          <div className="space-y-1">
            <p>Fix these rows and re-select the file (nothing was imported):</p>
            <ul className="list-disc pl-5 text-sm">
              {errors.map((err) => (
                <li key={`${err.line}-${err.message}`}>
                  Line {err.line}: {err.message}
                </li>
              ))}
            </ul>
          </div>
        </Alert>
      )}

      {rows.length > 0 && errors.length === 0 && (
        <>
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Item</TH>
                  <TH>Period start</TH>
                  <TH>Quantity</TH>
                  <TH>Notes</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((r, i) => (
                  <TR key={`${r.item_id}-${r.period_start}-${i}`}>
                    <TD>{items.find((it) => it.id === r.item_id)?.sku ?? r.item_id}</TD>
                    <TD>{r.period_start}</TD>
                    <TD>{r.quantity}</TD>
                    <TD className="text-muted-foreground">{r.notes ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
          <Button onClick={() => importCsv.mutate()} loading={importCsv.isPending}>
            Import {rows.length} row{rows.length === 1 ? "" : "s"} from {fileName}
          </Button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire into Planning**

In `frontend/src/pages/Planning.tsx`:

Add the import:

```tsx
import { ImportForecastsCsvSection } from "./planning/ImportForecastsCsv";
```

Inside the "Demand forecasts" card's `CardContent`, after the closing `</TableWrap>` of the forecast table, add:

```tsx
          <ImportForecastsCsvSection items={items.data ?? []} />
```

(`items` is the existing `useQuery<ItemResponse[]>` already in the file; `ItemResponse` has `id` and `sku`, which satisfies the prop type.)

- [ ] **Step 3: Verify with the type gate and tests**

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds, all tests pass

- [ ] **Step 4: Run the full backend suite once more**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/planning/ImportForecastsCsv.tsx frontend/src/pages/Planning.tsx
git commit -m "feat: csv forecast import on planning page"
```

---

## Verification checklist (after all tasks)

- `cd backend && .venv/Scripts/python.exe -m pytest -q` passes.
- `cd frontend && npm run build && npx vitest run` passes.
- Manual smoke via dev server: Planning shows the two new sections; Compute suggestions renders grouped weekly rows; accepting invalidates and refreshes the MRP plan table; CSV import previews and imports.
- `graphify update .` after the code lands (project rule).
