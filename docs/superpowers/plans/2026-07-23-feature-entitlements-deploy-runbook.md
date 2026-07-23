# Feature Entitlements — Deploy Runbook

One-time manual steps required the first time `b2e9f4a71c83_org_features_user_staff.py` runs
against production. Skipping these locks the product: every org defaults to `enabled_features=[]`
(403 on every gated route) and no user defaults to `is_staff=True` (nobody can reach
`/admin/customers` to fix that).

1. **After the migration runs**, connect to the production DB directly and flip one account to
   staff:
   ```sql
   UPDATE users SET is_staff = true WHERE email = '<your EvoWerk email>';
   ```
2. **Log in as that account**, go to `/admin/customers`, and for each existing org at the time of
   this deploy, use "Edit features" to set their `enabled_features` to whatever they were actually
   sold (Core, or Core + Premium — see `backend/app/core/features.py`'s `CORE_FEATURES`/
   `PREMIUM_FEATURES`). If there are zero or very few production customers at deploy time, this is
   a couple of clicks, not a script — a scripted backfill is unnecessary at current scale
   (`ponytail: manual backfill via /admin/customers UI; revisit only if the number of pre-existing
   orgs at some future migration makes clicking through each one impractical`).

Both steps are one-time — every org created after this deploy goes through `/admin/customers`
onboarding, which sets `enabled_features` at creation time, and `is_staff` accounts are set up once
per EvoWerk employee, not per deploy.
