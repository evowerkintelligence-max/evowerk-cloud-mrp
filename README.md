# EvoWerk Cloud MRP

Multi-tenant, cloud-hosted manufacturing system (BOM, procurement, lot tracking,
forecasting). Industry-agnostic core; industry-specific customization (pharma,
food, auto, etc.) is added later, config-driven, per customer.

This is a separate product/repo from the EvoWerk desktop app (NovaEdgeElectron).
No code is shared beyond common pip packages for core math (data cleaning,
OEE calc, forecasting) — see the desktop repo's `project_product_separation`
notes for why.

## Quickstart (fresh clone)

Needs Python 3.12, Node 20 and Docker. From the repo root:

```bash
make install   # `npm ci` (root npm workspace) + backend deps from the pinned lockfile
make test      # backend pytest (in-memory SQLite + fake Redis) + frontend vitest
make lint      # ruff + eslint
make typecheck # tsc -b over the frontend
make build     # production frontend build + docker images
cp .env.example .env   # fill in the three values, then:
make up        # Postgres, Redis, migrations, backend :8000, frontend :5173
```

No `make` (plain Windows)? The root `package.json` has the same targets:
`npm run setup`, `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`,
`npm run up`. The root `package.json` is an npm workspace over `frontend/`,
so there is one `package-lock.json`, at the repo root. CI runs these same
commands on every push and pull request.

## Current state

Security foundation: auth (OAuth2 password grant + JWT + TOTP MFA),
multi-tenant data model (`org_id` scoping baked into every table from day
one), an append-only audit log, and CI/CD security gates (see below). Built
on top of that, a full manufacturing feature set:

- **MRP core**: BOMs/recipes with revisioning, netted-vs-gross MRP explosion,
  build calculator, unit conversion on BOM lines.
- **Procurement**: purchase orders, suppliers, preferred-supplier selection,
  MOQ rounding, auto-generated POs off the MRP plan.
- **Inventory**: on-hand tracking, lot/FEFO consumption, multi-location,
  concurrency-safe stock adjustments, full audit trail.
- **Demand forecasting**: moving average / exponential smoothing, feeding the
  MRP plan and PO generation.
- **Shop-floor analytics**: OEE, SPC control charts, scrap, downtime
  (Pareto/MTTR), throughput, shift comparison, a rolled-up plant summary.
- **Quality & RCA**: defect-rate/DPU analysis, root-cause cases with
  fishbone-style cause trees and CAPA actions.
- **Predictive maintenance**: machine risk scoring and maintenance
  scheduling.
- **Computer vision**: image-based defect/PPE detection via Gemini vision.
- **AutoML**: sklearn model bake-off, SHAP explainability, drift detection.
- **BI / ad-hoc analytics**: dataset upload & cleaning, pivot/SQL query,
  custom dashboards/charts, KPIs, saved reports with AI narration, and a
  natural-language "Ask" chat over the data.
- **Data integrations**: CSV/Excel, SQL Server, Oracle, OPC-UA, OPC-DA, MQTT,
  generic REST/SAP OData, and OSIsoft PI historian connectors.
- **Workflow automation**: DAG-based workflow builder with run history, plus
  realtime streams and alert rules (inventory, downtime, quality, production
  target).
- **Process mining**: discovers actual process flow from event logs.

Over 100 backend test modules and 18 frontend test files cover this surface,
in addition to the auth suite described under Testing below.

### Audit log: append-only at the database level

`audit_logs` rows can only be inserted, never updated or deleted — enforced
by a Postgres trigger (migration `0c1e0833260f`), not just app-layer
discipline (the app never issues UPDATE/DELETE against this table either, but
the DB-level trigger is what holds even if the app has a bug or is
compromised). Only a Postgres superuser bypassing triggers entirely could get
around it; that's the accepted trust boundary. CI's `migration-check` job
proves this against a real Postgres instance on every push (inserts a row,
then asserts both UPDATE and DELETE raise).

### CI/CD security gates

Every push/PR runs lint (ruff, eslint), the backend and frontend test suites
(backend with a coverage floor), a production frontend build, and:
- **SAST**: `bandit` over `app/` (backend).
- **Dependency scanning**: `pip-audit` (backend) and `npm audit
  --audit-level=high` (frontend).
- **Secret scanning**: `detect-secrets` against a checked-in
  `.secrets.baseline` — new secrets fail the build; known-safe dev/CI
  placeholders (e.g. `postgres:postgres`, the CI-only JWT/Fernet keys) are
  whitelisted in the baseline.
- **Container scanning**: Trivy scans the backend image, the local-dev
  frontend image, and the production frontend image (see below) for
  HIGH/CRITICAL OS + dependency CVEs.
- **Migration check**: applies every Alembic migration against a real
  Postgres service container (the test suite itself runs against SQLite, so
  this is the only place migrations are validated against the database
  engine they're actually written for) and verifies audit-log immutability.
- **IaC checks**: `terraform fmt -check`, `terraform validate` and a Trivy
  config scan (HIGH/CRITICAL) for both `infra/terraform/aws` and
  `infra/terraform/azure`. Accepted findings are suppressed inline with a
  `# trivy:ignore:<ID> <reason>` comment on the resource.
- **Lockfile check**: `uv lock --check` fails if `backend/pyproject.toml`
  changed without regenerating `backend/uv.lock` (`make lock` or `uv lock`).
  Dependabot opens weekly update PRs for uv, npm, Actions, Docker and
  Terraform.

Locally, install the pre-commit hooks so the same secret-scan/lint checks run
before you commit, not just in CI:

```bash
cd backend && uv sync && cd ..
uv run --project backend pre-commit install
```

If you add a new, real config value that looks like a secret, run
`detect-secrets scan --baseline .secrets.baseline` and commit the updated
baseline; `detect-secrets audit .secrets.baseline` lets you review/label
findings interactively.

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
cp .env.example .env    # set POSTGRES_PASSWORD, JWT_SECRET_KEY, MFA_ENCRYPTION_KEY
docker compose up --build
```

This starts Postgres + Redis, runs Alembic migrations, and starts the backend
(`:8000`) and frontend (`:5173`). Compose refuses to start while any of the
three values in `.env` is empty, and the backend fails fast on startup if
`MFA_ENCRYPTION_KEY` isn't a valid Fernet key, by design.

Or run each piece manually:

```bash
# Backend
cd backend
uv sync                 # creates .venv from uv.lock, dev tools included
cp .env.example .env    # edit JWT_SECRET_KEY, MFA_ENCRYPTION_KEY, and DATABASE_URL
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# Frontend (npm workspace: install from the repo root, one lockfile there)
cd ..
npm ci
npm --workspace frontend run dev
```

### Logging and error tracking

The backend logs one JSON object per line to stdout, each tagged with the
request's `request_id` (taken from an incoming `X-Request-ID` header or
generated, and echoed back on the response) and the caller's `org_id`, so
your log pipeline can filter one request or one tenant. `LOG_LEVEL` sets the
level. Setting `SENTRY_DSN` turns on Sentry error tracking; it is off by
default and should point at your own Sentry (self-hosted or your account),
since EvoWerk never receives customer data. PII sending stays disabled.

## Production frontend image

`frontend/Dockerfile` (used by `docker-compose`) is dev-only — it runs the
Vite dev server for HMR. `frontend/Dockerfile.prod` is the real deployment
artifact: a multi-stage build (`npm run build` in a `node:20-alpine` stage,
then the static `dist/` output served by `nginx:1.27-alpine` — no Node/npm/
devDependencies in the final image at all).

The app calls the backend via same-origin relative paths (e.g.
`fetch("/auth/refresh")` in `src/api/client.ts`) rather than an absolute API
URL, mirroring `vite.config.ts`'s dev-mode proxy. In production, nginx plays
the same role: `nginx.conf.template` reverse-proxies `/auth/` and `/orgs/` to
`$BACKEND_UPSTREAM` (defaults to `backend:8000`, override per deployment) and
falls back to `index.html` for everything else (client-side routing). Adding
a new backend router means adding its prefix to both `vite.config.ts` (dev)
and `nginx.conf.template` (prod).

```bash
docker build -t evowerk-frontend-prod -f frontend/Dockerfile.prod .   # repo root context
docker run -p 8080:80 -e BACKEND_UPSTREAM=host.docker.internal:8000 evowerk-frontend-prod
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
cd backend && pytest -q                  # ~1000 tests; add --cov=app for coverage
npm --workspace frontend test           # vitest
npm --workspace frontend run test:coverage  # vitest with v8 coverage
```

`backend/tests/conftest.py` supplies test-only defaults for every required
secret, so no `.env` is needed to run the suite.

Tests run against an in-memory SQLite database (via `aiosqlite`) and an
in-memory fake Redis (via `fakeredis`), not real Postgres/Redis, so no
external services are required. Coverage: register → login → MFA enroll →
login-with-MFA → refresh (rotates, old token revoked) → logout (revokes
token), account lockout, password policy, the MFA-disable-bypass regression,
cross-tenant isolation, per-IP rate limiting, password reset request/confirm,
email verification request/confirm, and Google OAuth login (including the
verified-email auto-link gate). Microsoft OAuth shares the same callback
code path (`app/api/routes/oauth.py`) but isn't separately exercised by a
test yet.

## Deployment IaC

Terraform modules for customer-hosted deployment live under
`infra/terraform/`: `aws/` (ECS + ALB + RDS) and `azure/` (Container Apps +
Key Vault). See each module's `README.md` for prerequisites and the
deploy/update flow.

## Deferred / not yet built

- Secrets-manager integration — hosting is resolved (customer's own
  cloud infrastructure, not EvoWerk-managed; EvoWerk doesn't take on
  responsibility for customer data), so there's no EvoWerk-side secrets
  manager to integrate. Everything already funnels through one `Settings`
  object reading plain env vars (see `app/core/config.py`), which is exactly
  right for this model — the customer supplies those env vars via whatever
  their own cloud offers (AWS Secrets Manager, GCP Secret Manager, etc.).
- TLS termination — the customer's responsibility (their reverse proxy/load
  balancer); the app already doesn't attempt to terminate TLS itself.
- Encryption at rest for the Postgres volume itself (as opposed to the
  application-level encryption TOTP secrets already get) — the customer's
  managed-Postgres choice, not something EvoWerk builds.
- DAST (dynamic scanning, e.g. OWASP ZAP) — needs a running deployed target,
  so deferred until there's a staging environment; SAST/dependency/secret/
  container scanning all run in CI today (see above).
- Fine-grained RBAC (currently a single `role` string per user) — per-feature
  permission checks are not yet built.
- SOC2/GDPR/HIPAA/ISO 27001 evidence collection — not started.
- Account deactivation/invite UI — `is_active` exists and is enforced, but
  nothing sets it to `false` yet.
- Signed commits — not enforceable from inside the repo; requires a GitHub
  branch-protection rule (`Require signed commits`) turned on for this repo.
