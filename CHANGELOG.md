# Changelog

Notable changes, newest first. Update this file in the same commit as the
change it describes. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
- CI now runs ruff, eslint, vitest, the production frontend build, a
  lockfile-sync check, and a Trivy IaC scan of both Terraform modules.
- AWS ALB drops invalid HTTP headers; the ECS backend's EFS volume uses
  in-transit encryption.
- Split `automl_engine.py`, `DatasetDetail.tsx` and `Production.tsx` into
  smaller modules with no behavior change.

### Fixed
- `Sparkline` called `useId` after an early return (React rules-of-hooks).
