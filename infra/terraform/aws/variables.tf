variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short name used as a prefix for every resource this module creates"
  type        = string
  default     = "evowerk-mrp"
}

variable "environment" {
  description = "Deployment environment name (e.g. production, staging) - appended to resource names"
  type        = string
  default     = "production"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC this module creates"
  type        = string
  default     = "10.20.0.0/16"
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
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20
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
}

variable "db_name" {
  type    = string
  default = "evowerk_cloud_mrp"
}

variable "db_username" {
  type    = string
  default = "evowerk"
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
}

variable "backend_cpu" {
  description = "Fargate task CPU units for the backend service (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "backend_memory" {
  description = "Fargate task memory (MB) for the backend service"
  type        = number
  default     = 512
}

variable "frontend_cpu" {
  type    = number
  default = 256
}

variable "frontend_memory" {
  type    = number
  default = 512
}

variable "redis_cpu" {
  type    = number
  default = 256
}

variable "redis_memory" {
  type    = number
  default = 512
}
