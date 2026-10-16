# Contributing

## Setup

See the README "Quickstart". In short: `make install`, then `make test`,
`make lint` and `make typecheck` must pass before you open a pull request.
Install the git hooks once with `pre-commit install`.

## Branches and pull requests

- Branch from `master`; one topic per branch.
- Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `build:`, `refactor:`, `test:`, `perf:`).
  CI checks the title, and it becomes the squash-merge commit message.
- Add or update tests with every behavior change. CI enforces coverage floors
  (backend in `.github/workflows/ci.yml`, frontend in `frontend/vite.config.ts`).
- Note user-visible changes in `CHANGELOG.md` under "Unreleased".

## Releases

Tag `vMAJOR.MINOR.PATCH` on `master` after moving the "Unreleased" section of
`CHANGELOG.md` under the new version.
