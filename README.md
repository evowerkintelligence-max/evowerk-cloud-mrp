# EvoWerk Cloud MRP

Multi-tenant, cloud-hosted manufacturing system (BOM, procurement, lot tracking,
forecasting). Industry-agnostic core; industry-specific customization (pharma,
food, auto, etc.) is added later, config-driven, per customer.

This is a separate product/repo from the EvoWerk desktop app (NovaEdgeElectron).
No code is shared beyond common pip packages for core math (data cleaning,
OEE calc, forecasting) — see the desktop repo's `project_product_separation`
notes for why.

## Phase 0 (this repo, current state)

Foundation only: repo scaffold, auth (OAuth2 password grant + JWT + TOTP MFA),
multi-tenant data model (`org_id` scoping baked into every table from day one),
audit logging skeleton, and local dev setup. No business features yet.

Hardened after an initial security pass: account lockout after repeated failed
logins/MFA attempts, TOTP secrets encrypted at rest, MFA re-enrollment requires
password confirmation (and never disables an active MFA secret until the new
one is verified), timing-safe login for nonexistent users, a server-side
password policy, a unique constraint on `users.email`, an `is_active` flag
enforced on both login and token auth, and Redis-backed per-IP rate limiting
on register/login/refresh/mfa-verify.

The per-IP limits are deliberately generous (60 login attempts / 5 min per IP
by default, tunable via `RATE_LIMIT_*` env vars) — many real users can share
one public IP (office NAT, VPN, campus network), so the goal is to stop
large-scale credential stuffing, not to cap how many people can use the app
from behind the same router. Verified in tests that 15 concurrent users behind
one simulated IP all log in without being throttled. Being Redis-backed (not
in-process) also means the count is correct across every app instance/worker
once this runs behind a load balancer, not just on one box.

## Stack

- **Backend**: FastAPI, async SQLAlchemy 2.0, Alembic, Postgres, Redis (rate
  limiting only — no business data lives there).
- **Frontend**: React + TypeScript (Vite), TanStack Query (server state),
  Zustand (session state — access/refresh tokens are kept in memory, not
  localStorage, to reduce XSS token-theft blast radius).

## Local development

```bash
docker-compose up --build
```

This starts Postgres + Redis, runs Alembic migrations, and starts the backend
(`:8000`) and frontend (`:5173`). Before the first run, replace the
`MFA_ENCRYPTION_KEY` placeholder in `docker-compose.yml` with a real generated
key (see the comment next to it) — the backend fails fast on startup if it
isn't a valid Fernet key, by design.

Or run each piece manually:

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate  # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp .env.example .env    # edit JWT_SECRET_KEY, MFA_ENCRYPTION_KEY, and DATABASE_URL
alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## Secrets

All secrets come from environment variables via `app/core/config.py`'s
`Settings` object — `.env` locally (gitignored), real environment variables in
any deployed environment. `.env.example` documents every required variable.
No secrets-manager integration (Vault/Doppler) yet; that's deferred until a
hosting target is chosen, but funneling everything through one `Settings`
object keeps that a one-file change later.

TLS is not terminated by the app — it's expected to sit behind a reverse
proxy/load balancer in any real deployment.

## Testing

```bash
cd backend
pytest -q
```

Tests run against an in-memory SQLite database (via `aiosqlite`) and an
in-memory fake Redis (via `fakeredis`), not real Postgres/Redis, so no
external services are required. Coverage: register → login → MFA enroll →
login-with-MFA → refresh (rotates, old token revoked) → logout (revokes
token), account lockout, password policy, the MFA-disable-bypass regression,
cross-tenant isolation, and per-IP rate limiting (both that it trips, and that
it doesn't get in the way of many legitimate concurrent users).

## Deferred past Phase 0

- Secrets-manager integration (Vault/Doppler).
- TLS termination / hosting choice (EvoWerk-managed vs. customer's cloud).
- Fine-grained RBAC (currently a single `role` string per user).
- Compliance tooling (SAST/DAST scanning, SOC2 evidence collection).
- Account deactivation/invite UI — `is_active` exists and is enforced, but
  nothing sets it to `false` yet.
