# Feature Entitlements Deploy Runbook

One-time manual step required the first time `b2e9f4a71c83_org_features_user_staff.py` runs
against production. The migration itself backfills every pre-existing org's `enabled_features` to
Core (so nobody gets 403'd on Core routes the instant this deploys), but no user defaults to
`is_staff=True`, so nobody can reach `/admin/customers` to grant Premium or adjust anything until
one account is flipped manually.

1. **After the migration runs**, connect to the production DB directly and flip one account to
   staff:
   ```sql
   UPDATE users SET is_staff = true WHERE email = '<your EvoWerk email>';
   ```
2. **Log in as that account**, go to `/admin/customers`, and for any existing org that was actually
   sold Premium (not just Core), use "Edit features" to add the Premium keys (see
   `backend/app/core/features.py`'s `CORE_FEATURES`/`PREMIUM_FEATURES`). Orgs on Core need no
   action; the migration already covers them.

This step is one-time, every org created after this deploy goes through `/admin/customers`
onboarding, which sets `enabled_features` at creation time, and `is_staff` accounts are set up once
per EvoWerk employee, not per deploy.
