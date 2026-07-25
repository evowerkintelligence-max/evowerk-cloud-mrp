# Feature Entitlements & Staff Onboarding — Design

## Problem

Every logged-in user sees the entire app today — `frontend/src/lib/nav.ts`'s `NAV_SECTIONS` is a
static list rendered identically for every org, and no backend route checks anything beyond the
existing per-org `role` (owner/manager/member via `require_role`, `backend/app/api/deps.py:54`).
There is no concept anywhere in the codebase of "this customer is entitled to X but not Y" —
`PRD.md`'s Core/Premium tiers (§3a) are a pricing document, not enforced logic.

There is also no way for EvoWerk staff to act across orgs. The only "admin" that exists is an org
owner managing their own org (`PATCH /orgs/me`, gated by `require_role("owner")`,
`backend/app/api/routes/orgs.py:46`). Customers currently create their own accounts via public
`POST /auth/register` (`backend/app/api/routes/auth.py:71`) with no staff involvement and no way
to pre-assign which features they get.

This spec adds: a per-org entitlement list, backend/frontend enforcement of it, a new staff-only
role, and a staff onboarding page to create new customer orgs (with their initial features) and
edit existing customers' features later.

## Scope decision: what happens to public self-registration

`POST /auth/register` and its underlying `UserManager.create()` (`backend/app/core/users.py:74`)
are the *only* mechanism that creates an `Org` + owner `User` together (org row, default
`Location`, owner role assignment, all in one transaction). Roughly 90 backend test files depend
on this exact endpoint through a shared `_register_and_login()` fixture helper pattern (see
`backend/tests/test_cbm_api.py:18` for one instance) to set up a tenant to test against.

Deleting or gating this endpoint behind `require_staff` would break that entire fixture pattern
across the test suite — a large, unrelated rewrite that doesn't serve this feature's actual goal
(controlling which paying customers exist and what they're entitled to). Instead:

- The frontend's public `/register` route and its link from the Login page are removed. There is
  no discoverable path to self-signup in the product anymore.
- The backend `POST /auth/register` endpoint is left in place, unlinked and still rate-limited
  (`_register_rate_limit`, already wired at `auth.py:69`) — tests keep using it exactly as today,
  zero test-fixture changes required.
- New customers are created exclusively through the new staff-only `POST /admin/customers`
  endpoint (below), which reuses the same `UserManager.create()` call internally.

This is a product-surface decision (no visible signup path, sales-led onboarding only), not a
hard technical lockout of the old endpoint. If a stricter guarantee is ever needed (e.g. env-gating
`/auth/register` off outside of tests), that's a follow-up, not part of this spec.

## Data model

Two additive columns, no new tables:

### `Org.enabled_features` (`backend/app/models/tenant.py`)

```python
enabled_features: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
```

The single source of truth for what an org can access. A "Core" / "Core + Premium" preset in the
onboarding UI is a client-side convenience that pre-fills this list before submit — nothing stores
a separate `plan` label, so the displayed plan and the actual entitlements can never drift apart.

### `User.is_staff` (`backend/app/models/user.py`)

```python
is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

Distinct from the existing per-org `role` column: `role` answers "what can this user do *within
their own org*," `is_staff` answers "is this an EvoWerk employee who can act *across every org*."
No UI sets this — it's flipped directly in the database for your own account (and any other
EvoWerk employee's) as a one-time operational step, not part of any endpoint in this spec.

Both columns need an Alembic migration (see recent precedent naming,
`backend/alembic/versions/f6a3c9d18e72_lots_location.py`), backfilling existing rows to
`enabled_features=[]` and `is_staff=False` — every existing org is unaffected until you explicitly
onboard/edit it.

## Feature registry

A **single Python source of truth**, new file `backend/app/core/features.py`:

```python
# key -> (label, tier). Only features that gate a whole router live here — always-on
# routers (System section, plus Datasets/SQL Editor/BI Dashboards) are absent by design.
FEATURES: dict[str, tuple[str, str]] = {
    "boms": ("Bills of Materials", "core"),
    "procurement": ("Procurement", "core"),
    "lots": ("Lots", "core"),
    "warehouse": ("Warehouse", "core"),
    "planning": ("Planning", "core"),
    "plant_summary": ("Plant Summary", "core"),
    "oee": ("OEE", "core"),
    "pace_tracking": ("Pace Tracking", "core"),
    "downtime": ("Downtime", "core"),
    "downtime_tracking": ("Downtime Tracking", "core"),
    "throughput": ("Throughput", "core"),
    "scrap": ("Scrap & Quality", "core"),
    "shifts": ("Shifts", "core"),
    "spc": ("SPC", "core"),
    "kpis": ("KPIs", "core"),
    "kpi_dashboards": ("KPI Dashboards", "core"),
    "reports": ("Report Builder", "core"),
    "data_chat": ("Ask (AI data chat)", "core"),
    "automl": ("AutoML", "premium"),
    "predictive": ("Predictive Maintenance", "premium"),
    "cbm": ("Predictive Maintenance (sensor data)", "premium"),
    "simulation": ("Simulation", "premium"),
    "rca_cases": ("Root Cause Analysis", "premium"),
    "mfg_forecast": ("Manufacturing Forecast", "premium"),
    "computer_vision": ("Computer Vision", "premium"),
}

CORE_FEATURES = [k for k, (_, tier) in FEATURES.items() if tier == "core"]
PREMIUM_FEATURES = list(FEATURES.keys())  # Core + Premium = everything
```

`predictive` and `cbm` are two separate keys (two separate routers,
`backend/app/api/routes/predictive.py` and `cbm.py`) even though they're one product concept
("Predictive Maintenance") — gating happens at the router level (see below), so a router with no
matching key would silently stay ungated. Both are Premium.

The frontend needs the same classification for nav filtering and the onboarding checkboxes. There
is no build-time sharing between a Python dict and a TS file in this codebase, so a second,
hand-kept file mirrors it: `frontend/src/lib/features.ts`, same keys, same tiers, with a top comment
pointing at `backend/app/core/features.py` as the source of truth. Keeping these in sync is manual
— a test (see Testing) fails loudly if they diverge.

## Backend enforcement

### `require_feature(key: str)` (`backend/app/api/deps.py`, next to `require_role`)

```python
def require_feature(key: str):
    async def _check(
        current: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        org = (await db.execute(select(Org.enabled_features).where(Org.id == current.org_id))).scalar_one()
        if key not in org:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not included in your plan")
        return current
    return _check
```

One extra query, only on gated routes (mirrors `require_role`'s shape exactly; `get_current_user`
itself is unchanged, so every *ungated* route pays zero extra cost).

### `require_staff` (`backend/app/api/deps.py`, same file)

```python
async def require_staff(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")
    return current
```

### Wiring (`backend/app/main.py`)

Applied once per gated router at its existing `include_router()` call — the ~26 individual route
files in `backend/app/api/routes/` are untouched:

```python
app.include_router(boms.router, dependencies=[Depends(require_feature("boms"))])
app.include_router(oee.router, dependencies=[Depends(require_feature("oee"))])
# ...one line per FEATURES key, same pattern, mapping 1:1 to the table below...
app.include_router(cbm.router, dependencies=[Depends(require_feature("cbm"))])
```

Routers not in `FEATURES` (System section, `datasets`, `bi`, `orgs`, `auth`, etc.) keep their
current `include_router()` call unchanged — always-on.

## New admin endpoints (`backend/app/api/routes/admin.py`, new file)

All three gated by `require_staff`.

```python
router = APIRouter(prefix="/admin", tags=["admin"])

class CustomerCreateRequest(BaseModel):
    org_name: str
    owner_email: EmailStr
    enabled_features: list[str]

class CustomerResponse(BaseModel):
    org_id: str
    org_name: str
    owner_email: str
    enabled_features: list[str]

@router.post("/customers", response_model=CustomerResponse, status_code=201)
async def create_customer(
    body: CustomerCreateRequest,
    request: Request,
    current: CurrentUser = Depends(require_staff),
    user_manager: UserManager = Depends(get_user_manager),
    db: AsyncSession = Depends(get_db),
):
    # Reuses the exact org+owner-user creation path /auth/register already uses
    # (backend/app/core/users.py:74) - same Org+Location+owner-role transaction,
    # just with a throwaway password the customer never sees.
    throwaway_password = secrets.token_urlsafe(24)
    created_user = await user_manager.create(
        UserCreate(email=body.owner_email, password=throwaway_password, org_name=body.org_name),
        safe=True,
        request=request,
    )
    org = (await db.execute(select(Org).where(Org.id == created_user.org_id))).scalar_one()
    org.enabled_features = body.enabled_features
    await db.commit()
    # Sends the customer a real "set your password" email via the same flow
    # /auth/forgot-password uses (on_after_forgot_password, users.py:130) - no
    # password is ever communicated by staff.
    await user_manager.forgot_password(created_user, request)
    return CustomerResponse(
        org_id=str(org.id), org_name=org.name,
        owner_email=body.owner_email, enabled_features=org.enabled_features,
    )

@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(current: CurrentUser = Depends(require_staff), db: AsyncSession = Depends(get_db)):
    # Joins Org -> its owner User (role == "owner") for display; every org has exactly
    # one owner by construction (create_customer / the old register flow both set it).
    ...

@router.patch("/customers/{org_id}", response_model=CustomerResponse)
async def update_customer_features(
    org_id: uuid.UUID, body: CustomerFeaturesUpdate,
    current: CurrentUser = Depends(require_staff), db: AsyncSession = Depends(get_db),
):
    org = await get_or_404(db, Org, org_id, org_id, "Customer not found")  # cross-org lookup, no org_id filter
    org.enabled_features = body.enabled_features
    await db.commit()
    ...
```

`GET /orgs/me`'s existing `OrgResponse` (`orgs.py:18`) gains an `enabled_features: list[str]`
field (read-only — `OrgUpdateRequest` at `orgs.py:26` is untouched, so a customer's own
`PATCH /orgs/me` still can't self-grant features). This is how the frontend learns its own org's
entitlements.

`CurrentUser` (`deps.py:22`) gains `self.is_staff = user.is_staff`, and `GET /orgs/me`'s response
(or a new minimal `GET /auth/me`, whichever is cheaper to wire — implementer's call, both read the
same already-loaded `current.user`) exposes it, so the frontend knows whether to show the
`/admin/customers` nav link at all.

## Frontend

### Nav & route gating

`NavItem` (`frontend/src/lib/nav.ts:38`) gains an optional `featureKey?: string`, set on every
`NAV_SECTIONS` entry matching the registry table (Operations/Plant Analytics/Data & Reports'
Report-Builder-and-Ask/Intelligence items get one; System and the rest of Data & Reports don't).

A new `useEntitlements()` hook (colocated with the existing auth store pattern) fetches
`GET /orgs/me` once per session and exposes `hasFeature(key: string): boolean`. The sidebar filters
`NAV_SECTIONS` items through it before rendering.

`App.tsx` wraps every gated route in a new `<RequireFeature feature="oee">` component (mirrors the
existing `RequireAuth` at `App.tsx:98` exactly — same shape, checks `hasFeature` instead of
`accessToken`), rendering an `EmptyState` ("Not included in your plan") instead of `<Navigate>`,
since this isn't a "log back in" situation — the page exists, this org just can't use it.

### `/admin/customers` page (new, `frontend/src/pages/admin/Customers.tsx`)

Only reachable/linked if `is_staff` is true (checked the same way `RequireAuth` checks
`accessToken` — a `RequireStaff` wrapper, same pattern again). Two parts on one page:

1. **New customer** form: org name, owner email, a `CORE_FEATURES` / `PREMIUM_FEATURES` quick-pick
   (two buttons that just check/uncheck the matching boxes — not a stored choice) over individual
   checkboxes for every `FEATURES` entry, submit → `POST /admin/customers`.
2. **Existing customers** table below: one row per org (`GET /admin/customers`), each with an
   "Edit features" action opening the same checkbox set pre-filled with that org's current
   `enabled_features`, saving via `PATCH /admin/customers/{org_id}`.

## Testing

- Backend: `require_feature`/`require_staff` unit-style tests (403 without the flag/entitlement,
  200 with it) plus one test per new `/admin/customers` endpoint (staff can create/list/edit,
  non-staff gets 403, the created customer's org actually has the requested `enabled_features` and
  a real user row with `role="owner"`).
  A test asserting `FEATURES` keys in `backend/app/core/features.py` exactly match the keys in
  `frontend/src/lib/features.ts` — the file that would go silently stale otherwise. Since there's
  no cross-language import, this test shells out to read and parse both files' key lists (a plain
  string/regex extraction, not a real TS parser — good enough to catch drift, not to validate TS
  syntax).
- Frontend: no automated test (no component-render harness, per established repo convention) —
  manual smoke test: log in as a non-staff user, confirm `/admin/customers` isn't linked and
  direct navigation to it is blocked; log in as staff, create a customer with only `oee` +
  `downtime` enabled, confirm their nav shows only those two Plant-Analytics items and every other
  gated nav item is absent; confirm direct URL navigation to an ungated route
  (e.g. `/automl`) for that customer shows the "not included in your plan" state, not the page.

## Explicit non-goals (for this iteration)

- `POST /auth/register` is not deleted or hard-disabled — see Scope decision above.
- No self-serve upgrade flow (a customer clicking "upgrade to Premium" themselves) — entitlements
  are staff-set only, matching the sales-led motion described for the first customers.
- No billing/payment integration — this spec only controls feature *visibility and access*, not
  invoicing or payment collection for the tier a customer is on.
- No audit trail specific to feature changes beyond whatever `log_event` (already used elsewhere,
  e.g. `orgs.py:57`) records for free — a dedicated "entitlement change history" view is not built.
- No granular UI polish for the individual-feature checkboxes beyond a flat list — grouping them
  visually by the table's sections is a nice-to-have for the implementer, not a hard requirement.
