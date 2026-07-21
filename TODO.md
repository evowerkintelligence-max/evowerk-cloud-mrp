# TODO

## Deployment (customer self-hosted, before first release)

- [ ] **Persist dataset storage.** `backend/app/services/storage.py` writes uploaded
      datasets, cleaned/transformed datasets, and AutoML models to local disk
      (`{data_dir}/datasets/...`). `docker-compose.yml`'s `backend` service has no
      volume mount for that path (only `db_data` for Postgres is persisted) — every
      restart/redeploy wipes all customer data. Add a named volume for `data_dir`.
- [ ] **Frontend Dockerfile runs the Vite dev server in "production."**
      `frontend/Dockerfile` is `CMD ["npm", "run", "dev", "--", "--host"]`. Switch to
      a real build (`npm run build`) served by nginx or similar.

## Backend / API gaps

- [ ] **No way to delete a dataset.** There's no `DELETE /datasets/{id}` -
      found while cleaning up a test deployment where the dataset persistence
      fix above shipped after some datasets already existed, stranding their
      backing files on the old ephemeral disk (DB row survives, file doesn't -
      `GET /datasets/{id}` correctly 404s with "Dataset file is missing on
      disk", but the row itself can't be removed via the API). Add the
      endpoint, and have it also null out any `kpi_dashboards.kpi_ids` entries
      pointing at KPIs whose dataset gets deleted - deleting a dataset already
      cascades to KPIs/BI charts/alerts/AutoML runs/simulations/RCA cases that
      reference it (real FKs), but `kpi_dashboards.kpi_ids` is a plain array
      column, not an FK, so it doesn't get cleaned up and is left pointing at
      now-nonexistent KPI ids.
