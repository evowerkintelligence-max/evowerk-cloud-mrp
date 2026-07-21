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
