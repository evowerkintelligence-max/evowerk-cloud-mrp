# Changelog

Notable changes, newest first. Update this file in the same commit as the
change it describes. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Client error telemetry: the SPA reports render crashes, uncaught errors,
  unhandled rejections and 5xx/network API failures to `POST /client-errors`
  on the customer's own backend, which logs one structured line and forwards
  to Sentry only when `SENTRY_DSN` is set. Deduped and capped per page load,
  rate limited per IP, nothing stored.
- Terraform: TFLint (recommended preset + aws/azurerm rulesets) and native
  `terraform test` suites with mocked providers asserting security invariants
  (private databases, encryption, ingress), both run in CI. Input validation
  on key variables.
- `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, PR template, `.editorconfig`,
  and a CI check that PR titles follow Conventional Commits.

### Changed
- Backend dependencies moved to `backend/pyproject.toml` + `backend/uv.lock`
  (uv project, same pins); `make lock` is now `uv lock`, CI checks it with
  `uv lock --check`, Dependabot uses the `uv` ecosystem.
- Test passwords and CI app keys are generated per run; no reusable
  credential is committed.
- Frontend interaction tests (jsdom + Testing Library, `*.interaction.test.tsx`)
  for every page and shared component, using a shared fetch-stub harness in
  `frontend/src/test/renderPage.tsx`: 708 frontend tests, line coverage from
  16.7% to 98.8%. Coverage floor raised to lines 95 / functions 90 /
  branches 88.
- Explicit `typecheck` script (`npm run typecheck`, `make typecheck`) and a
  named Typecheck step in the frontend CI job.
- `infra/terraform/{aws,azure}/backend.tf.example`: encrypted remote state
  (S3 + KMS + DynamoDB lock; Azure Storage with Entra ID auth) with the
  one-time setup commands. Local state stays the default.
- Root `package.json` mirroring the Makefile targets (`npm run setup`,
  `npm test`, `npm run lint`, `npm run build`, `npm run up`) for machines
  without `make`.
- Frontend coverage floor (`frontend/vite.config.ts`), enforced in CI via
  `npm run test:coverage`.
- Optional outside-Docker variables (`DATABASE_URL`, `PORT`, `BACKEND_PORT`,
  `DEV_PASSWORD`) documented in the root `.env.example`.
- Root `Makefile` (`install`, `test`, `lint`, `build`, `up`, `lock`) and a
  Quickstart section in the README covering a fresh clone end to end.
- Root `.env.example` for `docker compose up`.
- Structured JSON logging on the backend with `request_id` and `org_id` on
  every line, an `X-Request-ID` response header, and opt-in Sentry error
  tracking (`SENTRY_DSN`, off by default).
- Pinned backend lockfiles (`backend/requirements.lock.txt`,
  `backend/requirements-dev.lock.txt`) used by CI and the backend image.
- Dependabot for pip, npm, GitHub Actions, Docker and Terraform.
- ESLint for the frontend and an explicit `backend/ruff.toml`.
- Coverage tooling: `pytest-cov` (backend, enforced floor in CI) and
  `@vitest/coverage-v8` (`npm run test:coverage`).

### Changed
- Second file-split pass: no source or test file is over 500 lines. Seven
  more backend test files, six services and six route modules split into
  siblings (import paths and the OpenAPI document unchanged); twelve
  frontend pages and the Planning render test split into per-page folders.
- Frontend coverage floor raised to lines 16 / functions 21 / branches 68,
  with unit tests for the report-section, simulation and PO helpers.
- Split `analytics/shared.tsx`, `RcaCases.tsx`, `Items.tsx` and `Boms.tsx`
  into per-page folders; import paths are unchanged.
- Split `services/reports.py` into a `reports/` package and moved helpers
  out of `routes/forecasts.py` and `routes/work_orders.py`; imports and the
  OpenAPI document are unchanged.
- Split `test_planning_grid.py` and `test_work_orders.py` into per-feature
  test files with shared helper modules (same 123 tests).
- Ruff config moved from `backend/ruff.toml` to the root `pyproject.toml`
  (same rules), so it applies from anywhere in the repo.
- CI path filters now only skip jobs on pull requests; pushes to `master`
  run every job.
- CI now runs ruff, eslint, vitest, the production frontend build, a
  lockfile-sync check, and a Trivy IaC scan of both Terraform modules.
- AWS ALB drops invalid HTTP headers; the ECS backend's EFS volume uses
  in-transit encryption.
- Split `automl_engine.py`, `DatasetDetail.tsx` and `Production.tsx` into
  smaller modules with no behavior change.

### Fixed
- Report builder: one report's AI narrative no longer carries over to the next
  report opened.
- MFA re-enrollment: the password prompt now waits for the full password
  instead of firing on the first keystroke.
- AutoML run page rendered its results twice, and the "earlier run" link
  pointed at a route that doesn't exist.
- Lot expiry was a day early for users west of UTC (date-only strings were
  parsed as UTC midnight).
- Dataset tall-to-wide pivot sent different columns than the form showed, and
  its default output name ignored the direction.
- KPIs, Integrations, Realtime and Alerts now show the server's error when an
  action fails instead of failing silently, and non-JSON error bodies show a
  readable message.
- Smaller: TV auto-refresh re-posts the last-run body; KPI breakdown no longer
  sends a stale value column; Realtime trims column names; Items history row
  spans every column; ItemSelect shows its own validation message; the to-buy
  panel's "POs created" result no longer sticks.
- `backend/dev_server.py` no longer imports the removed
  `inventory_snapshot` model.
- `Sparkline` called `useId` after an early return (React rules-of-hooks).
