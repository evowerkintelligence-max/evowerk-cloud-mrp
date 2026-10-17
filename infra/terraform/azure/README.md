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
- `ARM_SUBSCRIPTION_ID` exported (azurerm >= 4 no longer infers the
  subscription from the CLI): `export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)`
- Docker, to build and push `backend/Dockerfile` and
  `frontend/Dockerfile.prod` images

## Deploying

Same two-phase shape as the AWS module - the Container Apps need images that
already exist in ACR, but ACR is created by this same Terraform.

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars - at minimum: mfa_encryption_key, gemini_api_key

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

## Monitoring

[`monitoring.tf`](monitoring.tf) creates Azure Monitor metric alerts on
frontend 5xx responses, backend container restarts (crash loop / OOM),
Postgres CPU above 80% and Postgres storage above 85%. All route to one
action group; set `alarm_email` to add an email receiver, or add another
receiver (SMS, webhook, Teams) to the action group yourself.

## State

Same warning as the AWS module: no backend is configured in `versions.tf`,
so this defaults to local state - fine to experiment with, **not fine for a
real deployment**, since the state file holds the DB password and JWT secret
in plaintext. Before using this for real, switch to remote state:
[`backend.tf.example`](backend.tf.example) configures an Azure Storage
container (encrypted at rest by Azure Storage service encryption,
versioned, Entra ID auth instead of account keys, and locked with a blob
lease so concurrent applies can't corrupt it) and lists the one-time setup
commands. Copy it to `backend.tf`, fill it in, and run
`terraform init -migrate-state`.

## Updating

```bash
terraform apply -var image_tag=<new-tag>
```

Same migration caveat as the AWS module: the backend container's command
runs `alembic upgrade head` on every start - safe at `max_replicas = 1`
(hardcoded in `container_apps.tf`). If you ever scale the backend out, move
the migration to a separate one-off deploy step first.

## Scaling and cost

All three apps are pinned to `min_replicas = 0`, `max_replicas = 1`, sized
for a dev/test subscription where an idle environment should cost nothing
overnight. The tradeoffs:

- **Cold starts.** The first request after an idle period waits for a
  container pull, and for the backend also for `alembic upgrade head`. Set
  `min_replicas = 1` on the frontend and backend if that latency matters.
- **`max_replicas = 1` on `redis` is correctness, not cost.** Replicas don't
  share memory, so more than one would split the per-IP rate-limit counters.
  Leave it at 1 until Redis moves to Azure Cache for Redis.
- **Postgres has no scale-to-zero.** `B_Standard_B1ms` bills around the
  clock, so it's the bulk of an idle month. Stop it between sessions with
  `az postgres flexible-server stop -g <rg> -n <server>`; Azure restarts it
  automatically after 7 days, so it's not a permanent off switch.

## Testing and linting

CI (`terraform-lint` in `.github/workflows/ci.yml`) runs these on every
change under `infra/terraform/`, with no cloud credentials. To run them
locally from this directory:

```bash
terraform fmt -check -recursive   # formatting
terraform init -backend=false
terraform validate                # syntax + provider schema
terraform test                    # tests/*.tftest.hcl, see below
tflint --init && tflint --format compact
checkov -d . --framework terraform --compact --baseline .checkov.baseline
trivy config --severity HIGH,CRITICAL .
```

- **`terraform test`** plans the whole config against a mocked azurerm
  provider (`mock_provider`, Terraform >= 1.7) and asserts security
  invariants: Postgres has no public endpoint; the storage account blocks
  public blob access and requires TLS 1.2; Key Vault is RBAC-only and every
  backend secret is a Key Vault reference; only the frontend Container App
  has external ingress; and variable validation rejecting bad input.
- **TFLint** uses the `terraform` ruleset's `recommended` preset plus the
  `azurerm` ruleset, both configured and version-pinned in
  [`.tflint.hcl`](.tflint.hcl).
- **Checkov** fails on any finding not already accepted in
  [`.checkov.baseline`](.checkov.baseline); **Trivy** accepted findings are
  suppressed inline with a `# trivy:ignore:<ID>` reason.

<!-- BEGIN_TF_DOCS -->
## Inputs

| Name | Description | Type | Default | Required |
|---|---|---|---|:---:|
| `alarm_email` | Email that receives monitoring alerts (see Monitoring). Empty = alerts fire with no email receiver. | `string` | `""` | no |
| `azure_location` | Azure region to deploy into | `string` | `"eastus"` | no |
| `project_name` | Short name used as a prefix for every resource this module creates | `string` | `"evowerk-mrp"` | no |
| `environment` | Deployment environment name (e.g. production, staging) - appended to resource names | `string` | `"production"` | no |
| `vnet_cidr` | CIDR block for the VNet this module creates | `string` | `"10.30.0.0/16"` | no |
| `image_tag` | Image tag to deploy for both the backend and frontend ACR repos this module creates - must already be pushed before applying the Container Apps. | `string` | `"latest"` | no |
| `redis_image` | Redis image - pulled directly from Docker Hub (not mirrored into ACR), since it's an unmodified public image. | `string` | `"redis:7-alpine"` | no |
| `db_sku_name` | Postgres Flexible Server SKU | `string` | `"B_Standard_B1ms"` | no |
| `db_storage_mb` | Postgres Flexible Server storage in MB | `number` | `32768` | no |
| `db_name` | Name of the application database created on the Postgres Flexible Server | `string` | `"evowerk_cloud_mrp"` | no |
| `db_username` | Postgres administrator login (the password is generated and stored in Key Vault) | `string` | `"evowerk"` | no |
| `jwt_secret_key` | JWT signing secret. Leave empty to auto-generate a random one. | `string` | `""` | no |
| `mfa_encryption_key` | Fernet key encrypting TOTP secrets at rest. Must be supplied - can't be safely auto-generated here (has to be a valid Fernet key, not just random bytes). Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | `string` | n/a | yes |
| `gemini_api_key` | Gemini API key for the LLM-backed features (data chat, report narration, computer vision). Get one from https://aistudio.google.com/apikey | `string` | n/a | yes |
| `gemini_model` | Gemini model id. Pinned here so a retired model can be rolled forward without rebuilding the backend image - Google retires models for new API keys while still listing them in models.list(). | `string` | `"gemini-3.6-flash"` | no |
| `dataset_storage_quota_gb` | Size of the Azure Files share backing the backend's data_dir (uploaded datasets, AutoML artifacts). Standard file shares bill for what you use, not the quota, so this is a cap rather than a cost. | `number` | `100` | no |
| `backend_cpu` | vCPU for the backend Container App (Consumption plan requires specific valid cpu/memory pairs) | `number` | `0.5` | no |
| `backend_memory` | Memory for the backend Container App, paired with backend_cpu | `string` | `"1Gi"` | no |
| `frontend_cpu` | vCPU for the frontend (nginx) Container App | `number` | `0.25` | no |
| `frontend_memory` | Memory for the frontend Container App, paired with frontend_cpu | `string` | `"0.5Gi"` | no |
| `redis_cpu` | vCPU for the redis Container App | `number` | `0.25` | no |
| `redis_memory` | Memory for the redis Container App, paired with redis_cpu | `string` | `"0.5Gi"` | no |

## Outputs

| Name | Description | Sensitive |
|---|---|:---:|
| `frontend_url` | The app's HTTPS URL - Container Apps' default *.azurecontainerapps.io domain gets automatic HTTPS with zero setup, no custom domain needed to start. | no |
| `acr_login_server` | ACR login server to docker login and push images to | no |
| `acr_backend_repository` | ACR repository for the backend image | no |
| `acr_frontend_repository` | ACR repository for the frontend image | no |
| `postgres_fqdn` | Postgres Flexible Server FQDN, resolvable only inside the VNet | yes |
| `db_password` | Only needed if you need to connect manually - the app itself reads DATABASE_URL from Key Vault | yes |
| `key_vault_name` | Key Vault holding the app secrets | no |
<!-- END_TF_DOCS -->
