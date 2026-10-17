variable "azure_location" {
  description = "Azure region to deploy into"
  type        = string
  default     = "eastus"
}

variable "project_name" {
  description = "Short name used as a prefix for every resource this module creates"
  type        = string
  default     = "evowerk-mrp"

  validation {
    # ACR and storage account names strip hyphens and must be lowercase alphanumeric.
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.project_name))
    error_message = "project_name must start with a lowercase letter and contain only lowercase letters, digits and hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name (e.g. production, staging) - appended to resource names"
  type        = string
  default     = "production"

  validation {
    # ACR and storage account names strip hyphens and must be lowercase alphanumeric.
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.environment))
    error_message = "environment must start with a lowercase letter and contain only lowercase letters, digits and hyphens."
  }
}

variable "vnet_cidr" {
  description = "CIDR block for the VNet this module creates"
  type        = string
  default     = "10.30.0.0/16"

  validation {
    condition     = can(cidrhost(var.vnet_cidr, 0))
    error_message = "vnet_cidr must be a valid CIDR block (e.g. 10.30.0.0/16)."
  }
}

variable "image_tag" {
  description = "Image tag to deploy for both the backend and frontend ACR repos this module creates - must already be pushed before applying the Container Apps."
  type        = string
  default     = "latest"
}

variable "redis_image" {
  description = "Redis image - pulled directly from Docker Hub (not mirrored into ACR), since it's an unmodified public image."
  type        = string
  default     = "redis:7-alpine"
}

variable "db_sku_name" {
  description = "Postgres Flexible Server SKU"
  type        = string
  default     = "B_Standard_B1ms"
}

variable "db_storage_mb" {
  description = "Postgres Flexible Server storage in MB"
  type        = number
  default     = 32768
}

variable "db_name" {
  description = "Name of the application database created on the Postgres Flexible Server"
  type        = string
  default     = "evowerk_cloud_mrp"
}

variable "db_username" {
  description = "Postgres administrator login (the password is generated and stored in Key Vault)"
  type        = string
  default     = "evowerk"
}

variable "jwt_secret_key" {
  description = "JWT signing secret. Leave empty to auto-generate a random one."
  type        = string
  default     = ""
  sensitive   = true
}

variable "mfa_encryption_key" {
  description = "Fernet key encrypting TOTP secrets at rest. Must be supplied - can't be safely auto-generated here (has to be a valid Fernet key, not just random bytes). Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
  type        = string
  sensitive   = true

  validation {
    # A Fernet key is 32 bytes, url-safe base64 encoded: 43 chars plus "=".
    condition     = can(regex("^[A-Za-z0-9_-]{43}=$", var.mfa_encryption_key))
    error_message = "mfa_encryption_key must be a Fernet key (44 url-safe base64 chars); see the description for how to generate one."
  }
}

variable "gemini_api_key" {
  description = "Gemini API key for the LLM-backed features (data chat, report narration, computer vision). Get one from https://aistudio.google.com/apikey"
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  description = "Gemini model id. Pinned here so a retired model can be rolled forward without rebuilding the backend image - Google retires models for new API keys while still listing them in models.list()."
  type        = string
  default     = "gemini-3.6-flash"
}

variable "dataset_storage_quota_gb" {
  description = "Size of the Azure Files share backing the backend's data_dir (uploaded datasets, AutoML artifacts). Standard file shares bill for what you use, not the quota, so this is a cap rather than a cost."
  type        = number
  default     = 100

  validation {
    # Standard Azure file shares allow 1 GiB to 100 TiB.
    condition     = var.dataset_storage_quota_gb >= 1 && var.dataset_storage_quota_gb <= 102400
    error_message = "dataset_storage_quota_gb must be between 1 and 102400."
  }
}

variable "backend_cpu" {
  description = "vCPU for the backend Container App (Consumption plan requires specific valid cpu/memory pairs)"
  type        = number
  default     = 0.5
}

variable "backend_memory" {
  description = "Memory for the backend Container App, paired with backend_cpu"
  type        = string
  default     = "1Gi"
}

variable "frontend_cpu" {
  description = "vCPU for the frontend (nginx) Container App"
  type        = number
  default     = 0.25
}

variable "frontend_memory" {
  description = "Memory for the frontend Container App, paired with frontend_cpu"
  type        = string
  default     = "0.5Gi"
}

variable "redis_cpu" {
  description = "vCPU for the redis Container App"
  type        = number
  default     = 0.25
}

variable "redis_memory" {
  description = "Memory for the redis Container App, paired with redis_cpu"
  type        = string
  default     = "0.5Gi"
}

variable "alarm_email" {
  description = "Email that receives monitoring alerts (see monitoring.tf). Empty = alerts fire with no email receiver; subscribe one yourself."
  type        = string
  default     = ""
}
