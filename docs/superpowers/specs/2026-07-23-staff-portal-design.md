# Staff Portal (Separate Employee Auth) - Design

## Problem

The feature-entitlements PR (`docs/superpowers/specs/2026-07-23-feature-entitlements-onboarding-design.md`)
gives EvoWerk staff access by flipping `User.is_staff` on an otherwise-ordinary customer account,
logged into through the same `/auth/login` form every customer uses, landing in the same
`AppShell` with one extra "Customers" nav item. That account still belongs to an `Org`
(`user.org_id` is non-nullable) even though staff aren't scoped to any one org - it only works
because nothing currently checks that.

This spec replaces that with a dedicated employee identity: a "Staff Login" entry point on the
public login page, a separate employee-ID/password credential unrelated to any customer account,
and a standalone staff portal (customer list + per-feature toggles) reached only through it. The
regular customer app has no staff concept left in it at all afterward.

## Scope decision: this replaces `is_staff`, not just adds to it

Per your direction: the new employee login is the only door in. Concretely, this spec:

- Removes `User.is_staff` from the not-yet-merged migration entirely (edits
  `backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py` in place - nothing has
  shipped, so there's nothing to migrate away from).
- Removes `require_staff`, `CurrentUser`'s implicit staff-ness, and every `is_staff` reference in
  `backend/app/api/routes/orgs.py` and `backend/app/models/user.py`.
- Removes the "Staff" `NAV_SECTIONS` entry, `NavItem.requiresStaff` (added a few commits ago, now
  dead once "Customers" isn't a customer-app nav item anymore), `RequireStaff`, and the
  `/admin/customers` route from the customer-facing `App.tsx`.
- `backend/app/api/routes/admin.py`'s three endpoints keep their shape (create/list/edit
  customers) but move under the new `require_staff_session` dependency instead of `require_staff`.

## Data model

New table, no relation to `Org`/`User` - staff aren't tenant-scoped:

```python
# backend/app/models/staff.py
class StaffAccount(Base):
    __tablename__ = "staff_accounts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

`backend/alembic/versions/b2e9f4a71c83_org_features_user_staff.py` is edited (not superseded by a
new migration) to: keep the `orgs.enabled_features` column + Core-tier backfill exactly as-is, drop
the `users.is_staff` `op.add_column` entirely, and add `op.create_table("staff_accounts", ...)`
instead. One clean migration matching what actually gets built, since this hasn't deployed anywhere.

### Seeding (manual, per your answer)

A small script, `backend/scripts/create_staff_account.py`, run once per new hire:

```
python -m scripts.create_staff_account <employee_id>
```

Prompts for the password via `getpass` (never a CLI arg, so it can't leak into shell history),
hashes it with the existing `app.core.security.hash_password` (same argon2 `CryptContext` already
used for customer passwords - no new hashing scheme), and inserts the row. No endpoint, no UI -
matches "seeded manually for now."

## Backend auth

Mirrors the existing plain-PyJWT pattern in `app/core/security.py` (`create_access_token` /
`decode_token`, both already generic over a `"type"` discriminator field - see the existing
`"mfa_pending"` type there) rather than fastapi-users' `JWTStrategy`/`BaseUserManager`, which is
wired specifically to the customer `User` model and not worth bending for an unrelated principal.

```python
# app/core/security.py - new function alongside create_access_token
def create_staff_access_token(*, staff_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": staff_id,
        "type": "staff_access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
```

`decode_token` is reused unchanged (it's already just `jwt.decode`, not type-specific). The `"type"`
check is what makes a staff token and a customer access token mutually unusable on each other's
routes - a stolen customer token can't be replayed against `/admin/*`, and vice versa.

```python
# app/api/deps.py - new, alongside CurrentUser/get_current_user
class StaffSession:
    def __init__(self, staff: StaffAccount):
        self.id = staff.id
        self.employee_id = staff.employee_id


async def get_current_staff(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> StaffSession:
    credentials_error = HTTPException(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials")
    try:
        payload = decode_token(token)
        if payload.get("type") != "staff_access":
            raise credentials_error
        staff_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise credentials_error from exc

    staff = (await db.execute(select(StaffAccount).where(StaffAccount.id == staff_id))).scalar_one_or_none()
    if staff is None:
        raise credentials_error
    return StaffSession(staff)


require_staff_session = get_current_staff  # no roles/tiers among staff - one dependency is enough
```

`backend/app/api/routes/admin.py`'s `Depends(require_staff)` becomes `Depends(require_staff_session)`
everywhere, and handlers' `current: CurrentUser` params become `current: StaffSession`.
`log_event(..., actor_user_id=current.id, ...)` keeps working unchanged - `AuditLog.actor_user_id`
is a bare nullable UUID column with no FK, so a `StaffAccount.id` is exactly as valid there as a
`User.id` was.

### `POST /staff/auth/login` (new file, `backend/app/api/routes/staff_auth.py`)

```python
router = APIRouter(prefix="/staff/auth", tags=["staff-auth"])
_staff_login_rate_limit = rate_limit("staff_login", settings.rate_limit_login_per_5min, 300)


class StaffLoginRequest(BaseModel):
    employee_id: str
    password: str


class StaffTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=StaffTokenResponse, dependencies=[Depends(_staff_login_rate_limit)])
async def staff_login(body: StaffLoginRequest, db: AsyncSession = Depends(get_db)):
    staff = (
        await db.execute(select(StaffAccount).where(StaffAccount.employee_id == body.employee_id))
    ).scalar_one_or_none()
    ok = await run_in_threadpool(
        verify_password_timing_safe, body.password, staff.hashed_password if staff else None
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return StaffTokenResponse(access_token=create_staff_access_token(staff_id=str(staff.id)))
```

Reuses the existing `rate_limit` factory (same shape as `_login_rate_limit` in `auth.py`) and
`verify_password_timing_safe` (so a nonexistent `employee_id` costs the same argon2 work as a wrong
password - same enumeration protection customer login already has). No refresh token, no account
lockout, no MFA for v1 - see Non-goals; a short-lived access token is enough for a handful of
manually-seeded employees, and re-logging in every `access_token_expire_minutes` is a fine cost for
now.

## Frontend

### Login page

`Login.tsx` gains a small "Staff Login" link near the existing form (footer area, low visual
weight - this is an internal tool, not a customer-facing feature). Clicking it swaps the form body
in place (no route change, no modal overlay) to two fields, Employee ID and Password, posting to
`/staff/auth/login`. On success, the token is stored via a new store mirroring the existing
`useAuthStore` pattern exactly:

```typescript
// frontend/src/store/staffAuthStore.ts
interface StaffAuthState {
  accessToken: string | null;
  setToken: (token: string) => void;
  clear: () => void;
}
export const useStaffAuthStore = create<StaffAuthState>()(
  persist((set) => ({ accessToken: null, setToken: (t) => set({ accessToken: t }), clear: () => set({ accessToken: null }) }),
    { name: "staff-auth", storage: createJSONStorage(() => sessionStorage) }),
);
```

Kept fully separate from `useAuthStore` (different key, different shape, no refresh token) so a
customer session and a staff session can never be confused with each other client-side either.
Then navigates to `/staff`.

### `/staff` portal (new top-level route tree, outside the customer `App.tsx`/`AppShell`)

A minimal standalone shell - brand mark, "Log out" - wrapping today's `Customers.tsx` content
(moved to `frontend/src/pages/staff/Customers.tsx`), which already has the create-customer form and
the edit-features screen. The only UI change: `FeatureCheckboxes` becomes `FeatureToggles`, using a
new small `components/ui/switch.tsx` primitive (this codebase has `checkbox.tsx`/`badge.tsx` etc.
in that folder already but no switch) instead of raw `<input type="checkbox">` - same on/off
semantics, matches your "toggle" description, and follows the existing UI-primitives convention
this review already flagged `Customers.tsx` for not using.

A `RequireStaffSession` guard (checks `useStaffAuthStore`'s token is present, redirects to `/login`
if not - no server round-trip needed just to gate the route, the actual API calls will 401 on an
expired/invalid token regardless) wraps the `/staff/*` route tree.

### Removed from the customer app

- `RequireStaff`, the `/admin/customers` route (`App.tsx`).
- The "Staff" `NAV_SECTIONS` entry and `NavItem.requiresStaff` + its check in
  `isNavItemVisible`/`nav.ts` (dead once nothing sets it).
- `is_staff` from `CurrentUser`/`GET /orgs/me`'s response - customers' own session has zero
  staff-related fields left.

## Testing

- Backend: `test_staff_auth.py` - login succeeds with correct employee_id/password and returns a
  token whose `"type"` is `"staff_access"`; wrong password / unknown employee_id both 401
  identically (timing-safe path); a valid staff token is rejected by ordinary customer routes and a
  valid customer access token is rejected by `/admin/*` (the mutual-exclusion the `"type"` field is
  for).
- `test_admin_customers.py` is updated to obtain its token via `POST /staff/auth/login` against a
  `StaffAccount` fixture instead of registering a customer user and flipping `is_staff` - the
  non-staff-403 test becomes "no token / customer token → 401/403" instead of "non-staff user →
  403" (there's no such thing as a non-staff *user* anymore since staff aren't in the `User` table).
- Frontend: `tsc -b --noEmit` clean, plus a manual smoke test (log in as staff via the new link,
  create a customer, toggle a feature, confirm the customer's own login shows/hides nav
  accordingly - same check already done for the current PR, just through the new entry point).

## Explicit non-goals (for this iteration)

- No self-service "add another employee" screen - accounts are seeded via the CLI script only (per
  your answer). A UI for this is a follow-up if EvoWerk's headcount ever makes the script tedious.
- No staff account MFA, refresh tokens, or lockout-after-N-failures - the existing customer-auth
  hardening (`MfaChallenge`, refresh rotation, `failed_login_attempts`/`locked_until`) is not
  ported over for v1. Revisit if the staff portal's blast radius (any customer's entitlements)
  turns out to need it in practice.
- No employee-ID format validation beyond DB uniqueness (no fixed pattern like `EW-1234`) - staff
  can pick any string when seeding an account.
- No password reset flow for staff - a forgotten password is a re-run of the seeding script
  (updating the existing row) until this proves painful enough to build self-service.
