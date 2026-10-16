# AWS deployment (customer-hosted)

Terraform for standing up EvoWerk Cloud MRP on **your own** AWS account. Per
the hosting model: EvoWerk doesn't host or manage this infrastructure and
never has access to what runs here - you own it, you're responsible for it,
and this module is the starting point for your own ops team to take over.

Only AWS is covered right now. GCP/Azure equivalents don't exist yet - if
you need one, the shape should carry over (managed Postgres, a small Redis,
two container services behind a load balancer with TLS), just not written
using AWS Terraform provider resources.

## What this creates

- A VPC with public + private subnets across 2 AZs, one NAT gateway
- RDS Postgres 16, encrypted at rest, private subnets only
- Three ECS Fargate services: `frontend`, `backend`, `redis` (Redis holds
  only per-IP rate-limit counters, no business data - see the main repo
  README - so it doesn't need durability or backups)
- An ALB with HTTPS (ACM cert) in front of the frontend service only - the
  backend is never internet-facing; the frontend's nginx reverse-proxies
  `/auth/` and `/orgs/` to it internally (see `frontend/nginx.conf.template`),
  the same way Vite's dev proxy does locally
- Two ECR repositories (`backend`, `frontend`) with scan-on-push enabled
- Secrets Manager entries for the JWT secret, the Fernet key, and the DB
  connection string - the app already reads all of these from plain env
  vars (`app/core/config.py`'s `Settings`), so ECS's `secrets` block is the
  only wiring needed, no code changes

## What this does NOT create

- DNS records, if you leave `route53_zone_id` empty (see below)
- A CI/CD pipeline that builds and pushes images - that's still a manual
  `docker build` + `docker push` today (or wire up your own GitHub Actions
  job that does it)
- Multi-AZ RDS, WAF, autoscaling, or a second NAT gateway per AZ - all
  reasonable next hardening steps once there's a real customer depending on
  uptime, not included in this first pass

## Prerequisites

- Terraform >= 1.5
- An AWS account and credentials with permission to create everything above
- Docker, to build and push `backend/Dockerfile` and
  `frontend/Dockerfile.prod` images

## Deploying

This is a **two-phase apply** because of a real chicken-and-egg: the ECS
services need images that already exist in ECR, but the ECR repos are
created by this same Terraform.

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars - at minimum: domain_name, mfa_encryption_key

terraform init

# Phase 1: everything except the ECS services (which need images to exist)
terraform apply \
  -target=aws_ecr_repository.backend \
  -target=aws_ecr_repository.frontend

# Build + push both images (repeat whenever you have a new version)
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com

docker build -t <backend-repo-url>:latest ../../../backend
docker push <backend-repo-url>:latest

docker build -t <frontend-repo-url>:latest -f ../../../frontend/Dockerfile.prod ../../../frontend
docker push <frontend-repo-url>:latest

# Phase 2: everything else, now that images exist
terraform apply
```

(`<backend-repo-url>` / `<frontend-repo-url>` are the `ecr_backend_repository_url`
/ `ecr_frontend_repository_url` outputs from phase 1.)

## DNS + TLS

**If `route53_zone_id` is set:** fully automatic in the one `terraform apply`
above - ACM validation records and the app's A record both get created.

**If left empty (no Route53, or DNS lives elsewhere):** `terraform apply`
will create the ACM certificate in `PENDING_VALIDATION` and the
`aws_lb_listener.https` resource that references it can't finish until the
cert is issued. Workflow:

1. `terraform apply -target=aws_acm_certificate.main`
2. Read the `acm_certificate_validation_records` output, add those as CNAME
   records at whatever DNS provider you use
3. Wait for AWS to detect the validation (usually a few minutes)
4. Run `terraform apply` again for everything else
5. Point `domain_name` at the `alb_dns_name` output (a CNAME, since this
   isn't a Route53 ALIAS record)

## Email (password reset / verification)

Leave `ses_from_email` empty to skip - password-reset and verification links
are logged instead of emailed (see `backend/app/core/email.py`), fine for a
test deploy. To send real email:

1. Set `ses_from_email` in `terraform.tfvars` (e.g. `no-reply@mrp.customer.com`).
2. After `terraform apply`, read the `ses_domain_verification_record` output
   and add it as a TXT record (`_amazonses.<domain>`) at your DNS provider.
3. Wait for AWS to detect verification (Console: SES > Verified identities).
4. SES accounts start in sandbox mode (can only send to verified addresses) -
   request production access in the SES console before relying on this for
   real users.

## State

No backend is configured in `versions.tf` - it defaults to local state,
which is fine to experiment with but **not what you want for a real
deployment**: this state file contains the RDS password and JWT secret in
plaintext. Before using this for real, switch to encrypted remote state:
[`backend.tf.example`](backend.tf.example) configures S3 with KMS encryption,
versioning and a DynamoDB lock table, and lists the one-time setup commands.
Copy it to `backend.tf`, fill it in, and run `terraform init -migrate-state`.
Never commit `terraform.tfstate` (already gitignored here).

## Updating

New version of the app: build + push new image(s) with a new tag, then

```bash
terraform apply -var image_tag=<new-tag>
```

ECS replaces the running tasks with the new image. The backend task's
command still runs `alembic upgrade head` on every start (see `ecs.tf`) -
safe at `desired_count = 1`. If you ever scale the backend service beyond a
single task, move the migration to a separate one-off deploy step first, so
multiple tasks don't race to migrate at once.

<!-- BEGIN_TF_DOCS -->
## Inputs

| Name | Description | Type | Default | Required |
|---|---|---|---|:---:|
| `aws_region` | AWS region to deploy into | `string` | `"us-east-1"` | no |
| `project_name` | Short name used as a prefix for every resource this module creates | `string` | `"evowerk-mrp"` | no |
| `environment` | Deployment environment name (e.g. production, staging) - appended to resource names | `string` | `"production"` | no |
| `vpc_cidr` | CIDR block for the VPC this module creates | `string` | `"10.20.0.0/16"` | no |
| `availability_zones` | AZs to spread subnets across. Leave empty to auto-pick the first 2 available AZs in the region (2 is the minimum for RDS + the ALB). | `list(string)` | `[]` | no |
| `image_tag` | Image tag to deploy for both the backend and frontend ECR repos this module creates - must already be pushed before applying the ECS resources. | `string` | `"latest"` | no |
| `redis_image` | Redis image - pulled directly from Docker Hub (not mirrored into ECR), since it's an unmodified public image. | `string` | `"redis:7-alpine"` | no |
| `db_instance_class` | RDS instance class | `string` | `"db.t4g.micro"` | no |
| `db_allocated_storage` | RDS allocated storage in GB | `number` | `20` | no |
| `db_deletion_protection` | Block accidental RDS deletion via the AWS API/console. Leave true for a real deployment; set false only for a throwaway test env you intend to destroy/recreate on demand. | `bool` | `true` | no |
| `db_backup_retention_period` | RDS automated backup retention in days. AWS free-tier accounts cap this at 1 - raise it once off free tier. | `number` | `7` | no |
| `db_name` |  | `string` | `"evowerk_cloud_mrp"` | no |
| `db_username` |  | `string` | `"evowerk"` | no |
| `domain_name` | Domain the app will be served on (e.g. mrp.customer.com) - used for the ACM cert and CORS_ORIGINS. Leave empty to skip HTTPS/ACM entirely and serve plain HTTP on the ALB's own DNS name instead (e.g. for a quick test deploy with no domain). | `string` | `""` | no |
| `route53_zone_id` | Optional Route53 hosted zone ID for domain_name. If set, ACM DNS validation and the app's DNS record are created automatically in one apply. If left empty, the ACM validation CNAME records are printed as an output for you to add manually at whatever DNS provider you use, and you'll need a second `terraform apply` once the cert finishes validating - see README.md. | `string` | `""` | no |
| `ses_from_email` | Sender address for password-reset/verification emails (e.g. no-reply@mrp.customer.com) - must be on a domain you can add DNS records for, since SES requires domain verification. | `string` | `""` | no |
| `google_oauth_client_id` | Google OAuth 2.0 client ID (leave empty to disable Google login) | `string` | `""` | no |
| `google_oauth_client_secret` | Google OAuth 2.0 client secret | `string` | `""` | no |
| `microsoft_oauth_client_id` | Microsoft OAuth 2.0 (Azure AD app registration) client ID (leave empty to disable Microsoft login) | `string` | `""` | no |
| `microsoft_oauth_client_secret` | Microsoft OAuth 2.0 client secret | `string` | `""` | no |
| `jwt_secret_key` | JWT signing secret. Leave empty to auto-generate a random one (stored in Secrets Manager, never in state as anything other than the encrypted value Terraform already protects state values with). | `string` | `""` | no |
| `mfa_encryption_key` | Fernet key encrypting TOTP secrets at rest. Must be supplied - unlike jwt_secret_key this can't be auto-generated here, since it has to be a valid Fernet key, not just random bytes. Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | `string` | n/a | yes |
| `rate_limit_login_per_5min` | Login attempts allowed per IP per 5 minutes - matches the app's own default (see backend/app/core/config.py); raise only for a test env with an automated tester hitting this a lot. | `number` | `60` | no |
| `rate_limit_refresh_per_5min` | Token refresh calls allowed per IP per 5 minutes - matches the app's own default. | `number` | `120` | no |
| `rate_limit_register_per_hour` | Registrations allowed per IP per hour - matches the app's own default. | `number` | `20` | no |
| `rate_limit_mfa_verify_per_5min` | MFA verification attempts allowed per IP per 5 minutes - matches the app's own default. | `number` | `30` | no |
| `backend_cpu` | Fargate task CPU units for the backend service (256 = 0.25 vCPU) | `number` | `1024` | no |
| `backend_memory` | Fargate task memory (MB) for the backend service - the Fargate minimum (512) OOM-kills under real traffic: this app loads xgboost/lightgbm/shap/scipy/pandas/duckdb, which don't leave enough headroom once a few requests overlap. | `number` | `2048` | no |
| `frontend_cpu` |  | `number` | `256` | no |
| `frontend_memory` |  | `number` | `512` | no |
| `redis_cpu` |  | `number` | `256` | no |
| `redis_memory` |  | `number` | `512` | no |

## Outputs

| Name | Description | Sensitive |
|---|---|:---:|
| `alb_dns_name` | Point your DNS at this (CNAME) if route53_zone_id wasn't set | no |
| `acm_certificate_validation_records` | Only populated if domain_name was set and route53_zone_id was left empty - add these as CNAME records at your DNS provider, wait for validation, then re-apply. | no |
| `app_url` | Where to reach the app - the ALB DNS name over plain HTTP if domain_name was left empty, otherwise your domain over HTTPS. | no |
| `ecr_backend_repository_url` |  | no |
| `ecr_frontend_repository_url` |  | no |
| `rds_endpoint` |  | yes |
| `db_password` | Only needed if you need to connect manually - the app itself reads DATABASE_URL from Secrets Manager | yes |
<!-- END_TF_DOCS -->
