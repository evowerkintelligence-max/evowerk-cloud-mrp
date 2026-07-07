# Azure deployment (customer-hosted)

Terraform for standing up EvoWerk Cloud MRP on **your own** Azure account.
Same hosting model as `infra/terraform/aws` (see that module's README for the
full rationale) - EvoWerk doesn't host or manage this, you do.

## What this creates

- A VNet with two delegated subnets (Container Apps environment, Postgres
  Flexible Server)
- Postgres Flexible Server 16, VNet-integrated (no public endpoint at all),
  encrypted at rest by default (Azure-managed keys, no configuration needed)
- A Container Apps Environment with three apps: `frontend` (external
  ingress - the only one with a public HTTPS endpoint), `backend` (internal
  ingress only, reached via the frontend's nginx reverse proxy - see
  `frontend/nginx.conf.template` - the same way it works locally), `redis`
  (internal ingress only, holds only per-IP rate-limit counters, no business
  data - see the main repo README, so no durability requirements)
- Azure Container Registry (Basic SKU) for the backend/frontend images
- Key Vault holding the JWT secret, Fernet key, and DB connection string - a
  user-assigned managed identity grants the Container Apps read access; the
  app already reads all of these from plain env vars (`app/core/config.py`'s
  `Settings`), so this is wiring, not a code change

**No custom domain needed to start** - Container Apps' default
`*.azurecontainerapps.io` hostname gets automatic HTTPS with zero setup
(unlike the AWS module, which needs an ACM cert + DNS validation before it
has anything to serve over HTTPS at all). Add a custom domain + managed
certificate later, once there's a real domain to bind - not included here.

## Honest caveat: this is less proven than the AWS module

Every resource here is `terraform validate`-clean against the real `azurerm`
provider schema, but **the Redis container's TCP ingress
(`transport = "tcp"`, `exposed_port`) is the one piece of Azure-specific
behavior I'm least confident about** without testing against a real
subscription - Container Apps' TCP ingress support has had version-sensitive
rough edges. If applying this fails on the `redis` app specifically, that's
the first place to look; the fallback would be exposing Redis differently
(e.g. a plain Azure Cache for Redis instance instead of a container) if TCP
ingress doesn't behave the way this assumes.

## Prerequisites

- Terraform >= 1.5
- Azure CLI (`az`), logged in (`az login`) to the subscription you want to
  deploy into
- Docker, to build and push `backend/Dockerfile` and
  `frontend/Dockerfile.prod` images

## Deploying

Same two-phase shape as the AWS module - the Container Apps need images that
already exist in ACR, but ACR is created by this same Terraform.

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars - at minimum: mfa_encryption_key

terraform init

# Phase 1: everything except the Container Apps (which need images to exist)
terraform apply -target=azurerm_container_registry.main

# Build + push both images (repeat whenever you have a new version)
az acr login --name <acr-name-from-output>

docker build -t <acr-login-server>/backend:latest ../../../backend
docker push <acr-login-server>/backend:latest

docker build -t <acr-login-server>/frontend:latest -f ../../../frontend/Dockerfile.prod ../../../frontend
docker push <acr-login-server>/frontend:latest

# Phase 2: everything else, now that images exist
terraform apply
```

(`<acr-login-server>` is the `acr_login_server` output from phase 1.)

## State

Same warning as the AWS module: no backend is configured in `versions.tf`,
so this defaults to local state - fine to experiment with, **not fine for a
real deployment**, since the state file holds the DB password and JWT secret
in plaintext. Configure a remote backend (Azure Storage Account + a state
container, with access tightly restricted) before using this for real.

## Updating

```bash
terraform apply -var image_tag=<new-tag>
```

Same migration caveat as the AWS module: the backend container's command
runs `alembic upgrade head` on every start - safe at `min_replicas = 1`
(hardcoded in `container_apps.tf`). If you ever scale the backend out, move
the migration to a separate one-off deploy step first.
