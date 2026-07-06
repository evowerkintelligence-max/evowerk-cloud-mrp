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

## Stack

- **Backend**: FastAPI, async SQLAlchemy 2.0, Alembic, Postgres.
- **Frontend**: React + TypeScript (Vite), TanStack Query (server state),
  Zustand (session state — access/refresh tokens are kept in memory, not
  localStorage, to reduce XSS token-theft blast radius).

## Local development

```bash
docker-compose up --build
```

This starts Postgres, runs Alembic migrations, and starts the backend
(`:8000`) and frontend (`:5173`).

Or run each piece manually:

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate  # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp .env.example .env    # edit JWT_SECRET_KEY and DATABASE_URL
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

Tests run against an in-memory SQLite database (via `aiosqlite`), not
Postgres, so no external services are required. Coverage: register → login →
MFA enroll → login-with-MFA → refresh (rotates, old token revoked) → logout
(revokes token), plus a cross-tenant isolation check (a user in one org can't
read another org's data).

## Deferred past Phase 0

- Secrets-manager integration (Vault/Doppler).
- TLS termination / hosting choice (EvoWerk-managed vs. customer's cloud).
- Fine-grained RBAC (currently a single `role` string per user).
- Compliance tooling (SAST/DAST scanning, SOC2 evidence collection).
