# Mobile Exec Dashboard (React Native + Push Alerts)

Status: Approved for planning
Date: 2026-07-24

## Problem

Evowerk's analytics (OEE, Downtime, Alerts, KPI dashboards) are web-only
(`frontend/`, React/Vite). A manager/exec who isn't at a desk has no way to
check plant health or get notified when something breaches threshold except
opening the web app. Per [[teeptrak-comparison]], competitors lead their
mobile/exec story with exactly this: a pocket status view plus push
notification when something goes wrong.

## Scope

In scope: a native (Expo/React Native) mobile app, read-only, showing
already-configured KPI dashboard cards (which cover OEE/Downtime/any other
KPI an org has set up) and alert rule status, with login reusing the
existing JWT auth API, and push notifications when an alert rule fires.

Out of scope for v1: any write/mutation from the app (creating datasets,
editing KPIs or alert rules, acknowledging alerts), offline caching, tablet/
landscape-specific layouts, iPad-specific layout, in-app column-mapping UI
(mobile consumes existing `Kpi`/`AlertRule` configs made on the web app, it
doesn't create them), Android/iOS native modules beyond what Expo's managed
workflow provides, and any operator-tier / shop-floor workflow (pace
tracking, andon) - this is the manager/exec slice only.

## Why reuse the KPI dashboard system instead of raw OEE/Downtime endpoints

`POST /analytics/oee` and `/analytics/downtime`-family endpoints require the
caller to supply a dataset's column mapping
(`planned_time_col`/`operating_time_col`/etc, see `OeeComputeRequest` in
`backend/app/api/routes/oee.py`) on every call - a column-picker UI, fine on
desktop, wrong for a phone. `GET /kpi-dashboards/{id}/cards`
(`backend/app/api/routes/kpi_dashboards.py`) already returns fully-computed,
named, unit-labeled cards with breach `eval` state - the config (which
dataset, which columns, what threshold) lives in the `Kpi` row, set up once
on the web app. The mobile app is a pure consumer of whatever
`KpiDashboard`s (`tier="supervisor"`) an org has already built - no new
backend compute path needed for the dashboard screen.

## Data model

### New `DeviceToken`

```
id: uuid
org_id: uuid
user_id: uuid  # FK users.id
expo_push_token: str  # e.g. "ExponentPushToken[...]"
created_at: datetime
```

One row per (user, device) - re-registering the same token on login is an
upsert (unique on `expo_push_token`), not an append. New alembic migration
following the existing naming pattern (`backend/alembic/versions/`).

No new columns on `AlertRule`: whether to push is implicit - every user with
a registered token in the rule's org gets notified on trigger, same
all-org-members model the web app's alert list already uses (no
per-rule subscriber list in v1).

## Backend

### New `api/routes/device_tokens.py` (`/device-tokens`)

- `POST /device-tokens` `{expo_push_token: str}` - upsert for
  `current.user_id`/`current.org_id`; called by the app right after login
  and on app foreground (Expo push tokens can rotate). `require: get_current_user`.
- `DELETE /device-tokens/{token}` - called on logout, so a signed-out
  device stops receiving pushes.

### `services/alerts.py`

- New `_send_push(db, org_id, trigger)`, called alongside the existing
  `_fire_webhook` in `scan_all`'s per-trigger loop (same
  `# scan_all only - not scan_one` comment/reasoning applies - a
  keystroke-driven Test-button scan must not spam pushes). Looks up all
  `DeviceToken` rows for `org_id`, POSTs to Expo's push API
  (`https://exp.host/--/api/v2/push/send`) in one batched call (Expo's API
  accepts a list of messages per request - one HTTP call per scan, not one
  per token), body `{title: rule.name, body: trigger.message}` for each
  `triggered=True` result only (webhook fires every scan regardless of
  state per its own docstring; push should not - nobody wants a push every
  5 minutes confirming nothing is wrong).
  `ponytail: no delivery retry/dedup - same accepted-risk shape as
  _fire_webhook; add if proven insufficient.`
- Needs an `httpx` POST with a short timeout, same pattern as
  `_WEBHOOK_TIMEOUT`/`_fire_webhook` above it in the file.

## Mobile app (new `mobile/` directory, Expo/React Native + TypeScript)

- **Login screen**: email/password -> `POST /auth/login`
  (`backend/app/api/routes/auth.py`), store `access_token`/`refresh_token`
  in `expo-secure-store`. MFA (`mfa_required`) prompts a second TOTP code
  screen exactly like the web app's flow, reusing the same
  `/auth/login` call with `totp_code` filled in.
- **Token refresh**: `POST /auth/refresh` on 401, same refresh-token-rotation
  contract the web app already uses.
- **Push registration**: after login, request notification permission,
  get the Expo push token (`expo-notifications`), `POST /device-tokens`.
  `DELETE /device-tokens/{token}` on logout.
- **Dashboard screen**: `GET /kpi-dashboards` (org's dashboards),
  auto-select the first `tier="supervisor"` one (or a picker if more than
  one); `GET /kpi-dashboards/{id}/cards` on a pull-to-refresh + 60s poll
  interval (no websocket/SSE for v1 - polling matches the web app's
  `EngineScreen` auto-refresh model). Card grid: name, value, unit, breach
  color from `eval.status`.
- **Alerts screen**: `GET /alerts/rules` + `POST /alerts/scan`
  (`backend/app/api/routes/alerts.py`) on the same pull-to-refresh/poll,
  list of rules with triggered/ok state and `message`.
- **Push tap handling**: tapping a push notification deep-links to the
  Alerts screen.

## Testing

- Backend: new `test_device_tokens.py` (upsert-on-repeat-register, delete,
  org isolation - a token from org A never appears in org B's push batch).
  Extend `test_alerts.py` (or wherever `scan_all` is tested) with a case
  asserting `_send_push` is called once per triggered rule with registered
  tokens present, and not called for non-triggered rules or when no tokens
  exist for the org (mock the `httpx` POST, don't hit Expo's real API in
  tests - same mocking approach the webhook test already uses if one
  exists, verify via grep before writing).
- Mobile: no test framework installed yet (new project) - a small
  `App.test.tsx` smoke test (renders login screen) via `jest-expo`, Expo's
  standard default; nothing beyond that for v1. Manual verification via
  Expo Go during implementation (login, dashboard cards render, a real
  alert trigger produces a push on a physical device).
