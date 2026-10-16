# Security policy

## Reporting a vulnerability

Report privately through GitHub's **Security > Report a vulnerability** form on
this repository. Please do not open a public issue for a security problem.

Include the affected component (backend, frontend, infra), steps to reproduce,
and the impact you expect. We acknowledge reports within 3 working days and
share a fix timeline once the issue is confirmed.

## Supported versions

Only the latest release on `master` receives security fixes.

## How this repo guards itself

- Every deployment runs in the customer's own cloud account; Evowerk hosts no
  customer data (see `infra/terraform/*/README.md`).
- CI gates: bandit (SAST), pip-audit and `npm audit` (dependencies), Trivy
  (container images and IaC), Checkov (IaC policy), detect-secrets (pre-commit).
- Dependabot opens weekly update PRs for every ecosystem in the repo.
- Test credentials are generated per run (`backend/tests/_test_secrets.py`);
  no reusable password is committed.
