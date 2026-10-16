variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be an AWS region code such as us-east-1."
  }
}

variable "project_name" {
  description = "Short name used as a prefix for every resource this module creates"
  type        = string
  default     = "evowerk-mrp"

  validation {
    # Feeds ECR repo and other resource names, which only accept lowercase.
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.project_name))
    error_message = "project_name must start with a lowercase letter and contain only lowercase letters, digits and hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name (e.g. production, staging) - appended to resource names"
  type        = string
  default     = "production"

  validation {
    # Feeds ECR repo and other resource names, which only accept lowercase.
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.environment))
    error_message = "environment must start with a lowercase letter and contain only lowercase letters, digits and hyphens."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC this module creates"
  type        = string
  default     = "10.20.0.0/16"

  validation {
    # networking.tf carves /+8 subnets at indexes 0-1 and 100-101.
    condition     = can(cidrsubnet(var.vpc_cidr, 8, 101))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block of /24 or larger (e.g. 10.20.0.0/16)."
  }
}

variable "availability_zones" {
  description = "AZs to spread subnets across. Leave empty to auto-pick the first 2 available AZs in the region (2 is the minimum for RDS + the ALB)."
  type        = list(string)
  default     = []
}

variable "image_tag" {
  description = "Image tag to deploy for both the backend and frontend ECR repos this module creates - must already be pushed before applying the ECS resources."
  type        = string
  default     = "latest"
}

variable "redis_image" {
  description = "Redis image - pulled directly from Docker Hub (not mirrored into ECR), since it's an unmodified public image."
  type        = string
  default     = "redis:7-alpine"
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"

  validation {
    condition     = can(regex("^db[.][a-z0-9]+[.][a-z0-9]+$", var.db_instance_class))
    error_message = "db_instance_class must be an RDS instance class such as db.t4g.micro."
  }
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20

  validation {
    condition     = var.db_allocated_storage >= 20 && var.db_allocated_storage <= 65536
    error_message = "db_allocated_storage must be between 20 and 65536 GB (RDS Postgres limits)."
  }
}

variable "db_deletion_protection" {
  description = "Block accidental RDS deletion via the AWS API/console. Leave true for a real deployment; set false only for a throwaway test env you intend to destroy/recreate on demand."
  type        = bool
  default     = true
}

variable "db_backup_retention_period" {
  description = "RDS automated backup retention in days. AWS free-tier accounts cap this at 1 - raise it once off free tier."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_period >= 0 && var.db_backup_retention_period <= 35
    error_message = "db_backup_retention_period must be between 0 and 35 days."
  }
}

variable "db_name" {
  description = "Name of the application database created on the RDS instance"
  type        = string
  default     = "evowerk_cloud_mrp"
}

variable "db_username" {
  description = "RDS master username (the password is generated and stored in Secrets Manager)"
  type        = string
  default     = "evowerk"
}

variable "domain_name" {
  description = "Domain the app will be served on (e.g. mrp.customer.com) - used for the ACM cert and CORS_ORIGINS. Leave empty to skip HTTPS/ACM entirely and serve plain HTTP on the ALB's own DNS name instead (e.g. for a quick test deploy with no domain)."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Optional Route53 hosted zone ID for domain_name. If set, ACM DNS validation and the app's DNS record are created automatically in one apply. If left empty, the ACM validation CNAME records are printed as an output for you to add manually at whatever DNS provider you use, and you'll need a second `terraform apply` once the cert finishes validating - see README.md."
  type        = string
  default     = ""
}

variable "ses_from_email" {
  description = "Sender address for password-reset/verification emails (e.g. no-reply@mrp.customer.com) - must be on a domain you can add DNS records for, since SES requires domain verification."
  type        = string
  default     = ""

  validation {
    # ses.tf splits on "@" to get the SES domain.
    condition     = var.ses_from_email == "" || can(regex("^[^@]+@[^@]+[.][^@]+$", var.ses_from_email))
    error_message = "ses_from_email must be empty or an email address such as no-reply@mrp.customer.com."
  }
}

variable "google_oauth_client_id" {
  description = "Google OAuth 2.0 client ID (leave empty to disable Google login)"
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth 2.0 client secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "microsoft_oauth_client_id" {
  description = "Microsoft OAuth 2.0 (Azure AD app registration) client ID (leave empty to disable Microsoft login)"
  type        = string
  default     = ""
}

variable "microsoft_oauth_client_secret" {
  description = "Microsoft OAuth 2.0 client secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "jwt_secret_key" {
  description = "JWT signing secret. Leave empty to auto-generate a random one (stored in Secrets Manager, never in state as anything other than the encrypted value Terraform already protects state values with)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "mfa_encryption_key" {
  description = "Fernet key encrypting TOTP secrets at rest. Must be supplied - unlike jwt_secret_key this can't be auto-generated here, since it has to be a valid Fernet key, not just random bytes. Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
  type        = string
  sensitive   = true

  validation {
    # A Fernet key is 32 bytes, url-safe base64 encoded: 43 chars plus "=".
    condition     = can(regex("^[A-Za-z0-9_-]{43}=$", var.mfa_encryption_key))
    error_message = "mfa_encryption_key must be a Fernet key (44 url-safe base64 chars); see the description for how to generate one."
  }
}

variable "rate_limit_login_per_5min" {
  description = "Login attempts allowed per IP per 5 minutes - matches the app's own default (see backend/app/core/config.py); raise only for a test env with an automated tester hitting this a lot."
  type        = number
  default     = 60
}

variable "rate_limit_refresh_per_5min" {
  description = "Token refresh calls allowed per IP per 5 minutes - matches the app's own default."
  type        = number
  default     = 120
}

variable "rate_limit_register_per_hour" {
  description = "Registrations allowed per IP per hour - matches the app's own default."
  type        = number
  default     = 20
}

variable "rate_limit_mfa_verify_per_5min" {
  description = "MFA verification attempts allowed per IP per 5 minutes - matches the app's own default."
  type        = number
  default     = 30
}

variable "backend_cpu" {
  description = "Fargate task CPU units for the backend service (256 = 0.25 vCPU)"
  type        = number
  default     = 1024

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096, 8192, 16384], var.backend_cpu)
    error_message = "backend_cpu must be a valid Fargate CPU value: 256, 512, 1024, 2048, 4096, 8192 or 16384."
  }
}

variable "backend_memory" {
  description = "Fargate task memory (MB) for the backend service - the Fargate minimum (512) OOM-kills under real traffic: this app loads xgboost/lightgbm/shap/scipy/pandas/duckdb, which don't leave enough headroom once a few requests overlap."
  type        = number
  default     = 2048
}

variable "frontend_cpu" {
  description = "Fargate task CPU units for the frontend (nginx) service"
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096, 8192, 16384], var.frontend_cpu)
    error_message = "frontend_cpu must be a valid Fargate CPU value: 256, 512, 1024, 2048, 4096, 8192 or 16384."
  }
}

variable "frontend_memory" {
  description = "Fargate task memory (MB) for the frontend (nginx) service"
  type        = number
  default     = 512
}

variable "redis_cpu" {
  description = "Fargate task CPU units for the redis service"
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096, 8192, 16384], var.redis_cpu)
    error_message = "redis_cpu must be a valid Fargate CPU value: 256, 512, 1024, 2048, 4096, 8192 or 16384."
  }
}

variable "redis_memory" {
  description = "Fargate task memory (MB) for the redis service"
  type        = number
  default     = 512
}
