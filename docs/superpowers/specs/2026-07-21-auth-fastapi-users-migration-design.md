# Auth: Migrate to fastapi-users

Status: Approved for planning
Date: 2026-07-21

## Problem

Auth today (`backend/app/api/routes/auth.py`, `app/core/security.py`,
`app/models/user.py`) is hand-rolled: PyJWT access/refresh tokens, passlib
argon2 hashing, TOTP MFA, per-IP rate limiting, account lockout, audit
logging. It's well-tested (`backend/tests/test_auth.py` covers register,
login, MFA, refresh rotation, logout, lockout, password policy, an
MFA-disable-bypass regression, and cross-tenant isolation) but incomplete
and expensive to operate:

- No password reset, no email verification, no social login, no
  invite-a-teammate flow, no account deactivation UI.
- Seeding or fixing accounts requires raw SQL or one-off scripts (see the
  2026-07-21 TestSprite login incident: the AWS deployment had zero seeded
  users because `seed_dev_user.py` is a local-only script never wired into
  the deploy).
- Hand-rolled session/credential code carries more ongoing risk than a
  maintained library, even where it's currently correct.

This spec replaces the custom router with `fastapi-users`
(`fastapi-users[sqlalchemy]` + `httpx-oauth`), keeping every piece of
this app's actual domain logic (multi-tenant `Org`/`User` model, TOTP MFA,
audit logging, rate limiting, account lockout) as customizations layered on
top rather than a second parallel system.

## Scope

In scope:
- Full replacement of `backend/app/api/routes/auth.py`'s register/login
  endpoints with `fastapi-users`' routers, backed by `SQLAlchemyUserDatabase`
  over the existing `users` table.
- A custom `UserManager` (subclassing `fastapi-users.BaseUserManager`) that
  hooks in: org + default-location creation on register, TOTP MFA gating
  after password login, account lockout, and audit logging via the
  `on_after_*` hooks.
- Password reset and email verification flows (`fastapi-users` built-in),
  sending mail through a new `send_email(to, subject, body)` interface
  backed by AWS SES for AWS deployments.
- Google + Microsoft OAuth2 **login for existing accounts only** via custom
  callback routes, auto-linking to an existing account only when the
  provider's token asserts `email_verified=true`. This app's access model is
  admin-provisioned (a company's admin creates accounts for their
  employees via `/register`/a future invite flow) rather than public
  self-service signup, so OAuth must never create a new account or org for
  an email it doesn't already recognize - a stranger authenticating with
  their own Google account must not be able to mint themselves an org.
  Google/Microsoft login is a convenience for people who already have a
  provisioned account, not a second signup path.
- Two new `users` columns (`is_superuser`, `is_verified`) and a new
  `oauth_accounts` table, via Alembic migration.
- SES resources added to `infra/terraform/aws` (verified domain identity +
  IAM policy for the backend task).
- Frontend: "Forgot password?" flow, "Continue with Google/Microsoft"
  buttons on `Login.tsx`, an email-verification prompt, an OAuth callback
  route.

Out of scope for v1 (explicit decisions, not oversights):
- Refresh-token rotation stays exactly as it is today (`RefreshToken` model,
  `/auth/refresh`, `/auth/logout`) - `fastapi-users` core has no equivalent
  pattern, so this is left untouched rather than replaced with something
  weaker.
- Azure Communication Services / any Azure email wiring - the Azure
  Terraform module doesn't exist yet (`infra/terraform` only has `aws/`
  today). The email interface is provider-agnostic so this can be added
  later without touching auth code, but no Azure work happens in this pass.
- Existing users are grandfathered with `is_verified=True` by default -
  verification is only required going forward for newly registered
  accounts, so no deployed customer's existing users get locked out.
- No rehashing/migration of existing password hashes - `fastapi-users`'
  `PasswordHelper` is pointed at the same argon2 `CryptContext` already in
  use, so every currently-seeded user (including `dev@evowerk.dev` on the
  AWS deployment) keeps working unchanged.
- No invite-a-teammate flow, no account deactivation UI, no fine-grained
  RBAC - these remain on the "deferred / not yet built" list in the main
  README; this migration doesn't expand scope to cover them.
- No admin UI for user management - out of scope, unrelated to this
  library swap.

## Architecture

`fastapi-users` provides the router shells (`/auth/jwt/login`,
`/auth/register`, `/auth/forgot-password`, `/auth/reset-password`,
`/auth/request-verify-token`, `/auth/verify`, `/auth/{provider}/authorize`,
`/auth/{provider}/callback`) and the `UserManager`/`UserDatabase`
abstractions. Everything domain-specific plugs into that framework rather
than replacing it:

- **`UserDatabase`**: `SQLAlchemyUserDatabase` over the existing `User`
  model (extended with `is_superuser`, `is_verified`).
- **`UserManager`**: overrides `on_after_register` (create `Org` + default
  `Location`, matching today's `/auth/register` behavior exactly; call
  `log_event`), `on_after_login`/`on_after_forgot_password`/
  `on_after_reset_password`/`on_after_request_verify`/`on_after_verify`
  (all call `log_event`, matching today's audit trail), and `authenticate`
  (check `locked_until`/`failed_login_attempts` before/after password
  verification, same as today's `_is_locked`/`_register_failed_attempt`).
- **TOTP MFA**: unchanged logic (`generate_totp_secret`, `verify_totp`,
  `totp_provisioning_uri`, the `/mfa/enroll` and `/mfa/verify` endpoints
  stay as custom routes outside `fastapi-users`), but the point where it
  fires moves into a custom `JWTStrategy` wrapper: after `fastapi-users`
  confirms the password is correct, check `user.mfa_enabled` and either
  return the normal token pair or the existing `mfa_required: true`
  response shape.
- **Refresh tokens**: the existing `/auth/refresh` and `/auth/logout`
  routes and `RefreshToken` model stay as-is, mounted alongside
  `fastapi-users`' router. `_issue_tokens` becomes the bridge called from
  the custom login-success path.
- **Rate limiting**: the existing `rate_limit()` dependency factory gets
  attached to `fastapi-users`' login/register/forgot-password/reset-password
  routes the same way it's attached today.
- **OAuth**: `httpx-oauth.GoogleOAuth2` and `.MicrosoftGraphOAuth2` clients,
  wired via custom (not auto-generated) authorize/callback routes rather
  than `fastapi_users.get_oauth_router()`, so the callback can look up
  whether a matching account already exists *before* deciding anything:
  no existing account for that email -> reject (this app has no
  self-service OAuth signup, see Scope); existing account and the
  provider asserts the email is verified -> call
  `UserManager.oauth_callback(..., associate_by_email=True,
  is_verified_by_default=True)` to link/log in; existing account but the
  provider does not assert verification -> reject, without ever calling
  `oauth_callback()`. Tokens are then issued via the same `_issue_tokens()`
  the password-login path uses, so the response shape matches everywhere.

## Data model changes

```
users:
  + is_superuser  BOOLEAN NOT NULL DEFAULT false   -- fastapi-users schema requirement; app authorization still uses `role`, not this column
  + is_verified   BOOLEAN NOT NULL DEFAULT true     -- existing rows grandfathered true; UserManager sets false for new registrations

oauth_accounts (new, fastapi-users standard shape):
  id             UUID PK
  user_id        UUID FK -> users.id, CASCADE
  oauth_name     VARCHAR
  access_token   VARCHAR
  account_id     VARCHAR
  account_email  VARCHAR
  expires_at     INTEGER NULL
```

One Alembic migration. No changes to `orgs`, `locations`, `refresh_tokens`,
or any TOTP/lockout columns on `users`.

## Email interface

```python
# app/core/email.py
async def send_email(to: str, subject: str, body: str) -> None: ...
```

AWS implementation calls SES's `SendEmail` API via `boto3`, authenticated
through the ECS task's existing IAM role (`aws_iam_role.ecs_task` - the
same role future AWS-calling features attach policies to) rather than
managing SMTP credentials as a static secret. `fastapi-users`'
`on_after_forgot_password` and `on_after_request_verify` hooks call
`send_email()` with the reset/verification token embedded in a link back
to the frontend. SES requires a verified sending domain - the Terraform
module creates the identity and outputs the DNS records the customer must
add (same pattern already used for ACM certificate validation).

## Frontend

- `Login.tsx`: add "Forgot password?" link, "Continue with Google" /
  "Continue with Microsoft" buttons.
- New: `ForgotPassword.tsx` (request reset), `ResetPassword.tsx` (consume
  token + set new password), an email-verification banner/page, an OAuth
  callback route that exchanges the provider redirect for a session the
  same way password login does today.
- `authStore.ts`: unchanged.

## Testing

`backend/tests/test_auth.py`'s existing coverage (register, login, MFA
enroll/verify, login-with-MFA, refresh rotation + revocation, logout,
lockout, password policy, MFA-disable-bypass regression, cross-tenant
isolation, per-IP rate limiting) must keep passing with equivalent
behavior - this is the regression gate for the migration, run against the
new router rather than rewritten.

New tests: password reset request/confirm (including that a reset request
for a non-existent email doesn't reveal that, same enumeration-safety
principle as today's login), email verification request/confirm, OAuth
login success (mocked provider token response) for both providers, and
OAuth auto-link rejection when the provider's token asserts
`email_verified=false`.
