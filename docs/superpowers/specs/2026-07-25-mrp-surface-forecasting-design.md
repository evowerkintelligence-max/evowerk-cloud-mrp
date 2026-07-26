# MRP Phase 1: Surface Forecasting - Design

Date: 2026-07-25
Status: approved roadmap, spec pending review

## Context

The MRP improvement roadmap has four phases, each with its own spec and build cycle:

1. **Surface forecasting** (this spec): make the existing demand-forecasting intelligence visible and usable in Planning.
2. **Honest netting**: time-phased buckets (supply counted by `PurchaseOrder.expected_delivery_date`) and safety-stock awareness in the MRP engine.
3. **Explainable plan**: per-line provenance drill-down, action-only filtering, Build/Buy views, spend preview. After phase 2 so it renders the engine's final shape once.
4. **Work orders**: minimal WO object so Build lines close the loop the way Buy lines do via draft POs.

Today the backend already has:

- `GET /forecasts/suggested/{item_id}`: read-only projection from consumption history (moving average or exponential smoothing, 95% bounds, `services/demand_forecasting.py`). **No frontend caller.**
- `POST /forecasts/import`: atomic bulk upsert, source stamped `imported`, the desktop forecaster's integration point. **No frontend caller.**

So planners hand-type every forecast into a four-field form, one item and period at a time. Phase 1 turns that into review-and-confirm.

## Goal

A planner opens Planning, clicks one button, sees what history says demand will be for every item with consumption data, accepts the items they agree with, and optionally bulk-loads forecasts from a CSV. No silent overwrites, no double-counted demand.

## Non-goals

- No changes to the MRP netting engine (phase 2).
- No changes to `POST /forecasts/import` request/response shape (the desktop forecaster depends on it).
- No new forecasting models, seasonality, or trend handling.
- No inline editing of suggested quantities before accept (edit via the normal forecast list afterwards).
- No scheduled/automatic re-forecasting, no forecast-accuracy tracking, no per-location forecasts. All future candidates.

## Design

### Why weekly buckets

`compute_demand_forecast` returns one point per day (default 30-day horizon, flat level). Writing 30 daily rows per item would flood the forecast list and add nothing: the level is constant. The server aggregates daily points into 7-day buckets before anything is shown or written. Bucket k starts at `as_of + 1 + 7k`; the last bucket may cover fewer than 7 days. Bucket quantity is the sum of its daily `yhat` values; bucket lower/upper are the sums of the daily bounds. Summing bounds is correct here, not conservative: the forecast is a flat level, so daily errors are the same level error repeated, fully correlated, and interval widths add linearly.

### Backend

One new shared helper in `services/demand_forecasting.py`:

```
compute_weekly_suggestions(db, org_id, *, horizon_days, item_ids=None)
    -> list[ItemSuggestion]   # item, method_used, history_points, weekly points
```

Implementation: one call to `daily_consumption_by_item` (already supports `item_ids=None` for all items in one query), then the existing pure-Python forecast per item, then bucketing. Items with `method_used == "no_data"` are excluded. Complexity is one DB query plus O(items x lookback) Python.
<!-- ponytail: in-request loop over items; move to a background job if org catalogs reach tens of thousands of items -->

Two new routes in `api/routes/forecasts.py`:

**`GET /forecasts/suggested`** (auth: `get_current_user`, same as the per-item route)
Query params: `horizon_days` (default 30, validated 1..365, same rule as the per-item route).
Response: the suggestion window plus per-item suggestions with weekly points. The window fields exist so the client labels conflicts against exactly the dates the accept route will delete in, instead of re-deriving them from its own clock:

```json
{
  "window_start": "2026-07-26",
  "window_end": "2026-08-24",
  "items": [
    {
      "item_id": "...", "item_sku": "...", "item_name": "...",
      "method_used": "exp_smoothing", "history_points": 14,
      "points": [
        { "period_start": "2026-07-26", "quantity": "70.0", "lower": "51.2", "upper": "88.8" }
      ]
    }
  ]
}
```

Same field names as `SuggestedForecastResponse`; the docstring states points are weekly buckets, not days. The per-item daily route stays as is.

**`POST /forecasts/suggested/accept`** (auth: `require_role("owner")`, same as all forecast writes)
Body: `{ "item_ids": ["..."], "horizon_days": 30 }`, non-empty, max 500 items.
For each item, in one transaction covering the whole request (same all-or-nothing precedent as `/forecasts/import`):

1. Recompute the weekly suggestion server-side (quantities are server-authoritative; the client never sends numbers).
2. If the item now has no history (`no_data`), skip it, touch nothing, report it in `skipped`.
3. Delete every existing `DemandForecast` row for the item with `period_start` inside `[first bucket start, last bucket end]`. This is mandatory, not a convenience: `as_of` moves daily, so bucket boundaries shift, and plain upsert would leave yesterday's rows behind at the old boundaries, silently double-counting demand in MRP.
4. Upsert the weekly rows via the existing `_upsert_forecast`, `source="suggested"`, notes stamped `from history (exp_smoothing, 14 days)`.

Audit: one `log_event(action="forecast.suggested_accepted", metadata={items, deleted, created, updated, skipped})`.
Response: `{ "items": n, "deleted": n, "created": n, "updated": n, "skipped": [{item_id, item_sku}] }`.

`source="suggested"` requires no model change: `DemandForecast.source` is a free String(20); `manual`, `imported`, and now `suggested` each name their route of origin, which is the field's documented purpose.

### Frontend: Planning page

New card between the forecast-entry card and the MRP plan card: **"Suggest from history"**.

- Horizon input (days, default 30) plus a **Compute suggestions** button. No fetch on mount: it is an O(items) computation the planner triggers deliberately.
- Review table, one group per item: SKU and name, method badge, `history_points` shown as "N days of history", weekly rows with period, quantity, and the lower..upper range when present.
- Per-item checkbox (whole projections are accepted or not; no per-week picking):
  - **Checked by default** when the item has no existing forecast rows inside the suggestion window.
  - **Unchecked by default** otherwise, labeled "replaces N existing rows (M manual)". Computed client-side from the already-loaded forecast list by exact window overlap on `period_start`. This is the no-silent-overwrite rule, and it also covers the near-miss case: a manual row for the same week on a different day would otherwise double-count in MRP, so it must be flagged and replaced, not merely not-collided-with.
- **Accept selected** posts `item_ids` to `/forecasts/suggested/accept`, then invalidates the `forecasts` and `mrp-plan` queries and shows the counts (including skipped items). A 403 surfaces the API error as usual (viewer roles can review but not accept).

### Frontend: CSV import

Second addition to the forecast-entry card: **Import CSV**.

- Columns: `sku, period_start, quantity, notes` (notes optional). A "download template" link serves a static example string.
- File input, client-side parse with a small quote-aware CSV splitter (double-quoted fields so notes may contain commas; no external dependency).
- Preview table with per-row validation before anything is sent: unknown SKU (mapped against the already-loaded items list), bad date, negative or non-numeric quantity. Any invalid row blocks the import with the row number and reason, matching the backend's atomic all-or-nothing contract rather than fighting it.
- Import posts the mapped `item_id` rows to the existing `/forecasts/import` unchanged. Rows land with `source="imported"`, which is the correct provenance for a CSV. Result alert shows created/updated/total, then the same query invalidations.

## Edge cases

- Org with no consumption history: `GET /forecasts/suggested` returns `[]`; the card shows "No items have consumption history yet."
- Item consumed only before the 90-day lookback: excluded (`no_data`), same as above.
- Concurrent accepts of the same item: `_upsert_forecast` already handles the insert race with per-row savepoints; last writer wins, which matches the documented forecast-import semantics.
- Accept response `skipped` non-empty: shown in the result alert; those items keep their existing rows untouched.
- CSV with duplicate (sku, period) rows: allowed; the backend upserts in order, last row wins, consistent with import semantics today.

## Testing

Backend (pytest, same style as existing forecast tests):

- Bucketing math: 30-day horizon yields 5 buckets (4 full, 1 partial), bucket sums equal the daily total, bounds sum linearly, partial bucket sums only its days.
- `GET /forecasts/suggested`: excludes no-history items, respects `horizon_days` validation, org-scoped.
- Accept: deletes exactly the in-window rows (manual row inside window replaced; row outside window untouched), stamps `source="suggested"` and notes, skips no-data items without deleting, whole request atomic on failure, audit event written, owner-role enforced.

Frontend: mirror whatever page-level test convention exists for Planning; at minimum the conflict-labeling logic (checked vs unchecked defaults) as a pure function test if the page has no test harness.

## Out of scope, revisit when pulled for

Inline quantity editing before accept, seasonality/trend models, scheduled re-forecasts, forecast accuracy tracking (suggested vs actual), per-location forecasts, background computation for very large catalogs.
