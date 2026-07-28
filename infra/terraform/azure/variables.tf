variable "azure_location" {
  description = "Azure region to deploy into"
  type        = string
  default     = "eastus"
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

variable "vnet_cidr" {
  description = "CIDR block for the VNet this module creates"
  type        = string
  default     = "10.30.0.0/16"
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
  type    = string
  default = "evowerk_cloud_mrp"
}

variable "db_username" {
  type    = string
  default = "evowerk"
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
}

variable "backend_cpu" {
  description = "vCPU for the backend Container App (Consumption plan requires specific valid cpu/memory pairs)"
  type        = number
  default     = 0.5
}

variable "backend_memory" {
  type    = string
  default = "1Gi"
}

variable "frontend_cpu" {
  type    = number
  default = 0.25
}

variable "frontend_memory" {
  type    = string
  default = "0.5Gi"
}

variable "redis_cpu" {
  type    = number
  default = 0.25
}

variable "redis_memory" {
  type    = string
  default = "0.5Gi"
}
