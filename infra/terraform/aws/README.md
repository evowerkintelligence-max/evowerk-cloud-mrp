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
plaintext. Configure a remote backend (S3 + a DynamoDB lock table, or
Terraform Cloud) with tightly restricted access before using this for real,
and never commit `terraform.tfstate` (already gitignored here).

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
