# Feature Entitlements Deploy Runbook

One-time manual step required the first time `b2e9f4a71c83_org_features_user_staff.py` runs
against production. The migration itself backfills every pre-existing org's `enabled_features` to
Core (so nobody gets 403'd on Core routes the instant this deploys), but no `staff_accounts` row
exists yet, so nobody can reach `/staff` to grant Premium or adjust anything until one is seeded
manually.

1. **After the migration runs**, seed one staff account from a shell with production DB access:
   ```bash
   python -m scripts.create_staff_account <your-employee-id>
   ```
   (run from `backend/`; prompts for a password twice via `getpass`, matching the same argon2
   hashing customer passwords use)
2. **Go to the public login page, click "Staff Login"**, and log in with that employee ID. On
   `/staff`, for any existing org that was actually sold Premium (not just Core), use
   "Edit features" to add the Premium keys (see `backend/app/core/features.py`'s
   `CORE_FEATURES`/`PREMIUM_FEATURES`). Orgs on Core need no action; the migration already covers
   them.

This step is one-time, every org created after this deploy goes through `/staff` onboarding, which
sets `enabled_features` at creation time, and staff accounts are seeded once per EvoWerk employee,
not per deploy.
