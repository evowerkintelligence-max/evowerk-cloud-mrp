# Changelog

Notable changes, newest first. Update this file in the same commit as the
change it describes. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
- `backend/dev_server.py` no longer imports the removed
  `inventory_snapshot` model.
- `Sparkline` called `useId` after an early return (React rules-of-hooks).
