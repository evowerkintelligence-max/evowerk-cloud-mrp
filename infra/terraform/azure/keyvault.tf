# Key Vault names must be 3-24 chars - truncate rather than fail on a long
# project_name/environment combination. Global uniqueness collisions are rare
# and Azure will just error asking for a different name if it happens.
resource "azurerm_key_vault" "main" {
  name                = substr(replace("${local.name_prefix}-kv", "_", "-"), 0, 24)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  enable_rbac_authorization = true
}

resource "azurerm_user_assigned_identity" "container_apps" {
  name                = "${local.name_prefix}-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.container_apps.principal_id
}

resource "azurerm_role_assignment" "kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.container_apps.principal_id
}

# Separate from the read-only grant above - whoever/whatever runs `terraform
# apply` needs write access to actually create the secrets below.
resource "azurerm_role_assignment" "kv_secrets_officer_current" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

locals {
  jwt_secret_value = var.jwt_secret_key != "" ? var.jwt_secret_key : random_password.jwt_secret.result
  database_url     = "postgresql+asyncpg://${var.db_username}:${random_password.db_password.result}@${azurerm_postgresql_flexible_server.main.fqdn}/${var.db_name}"

  # Predictable ahead of the actual azurerm_container_app resources existing -
  # both only depend on the shared environment, not on each other, which
  # avoids a circular dependency (backend's CORS needs the frontend's URL,
  # frontend's BACKEND_UPSTREAM needs the backend's URL).
  frontend_url          = "https://frontend.${azurerm_container_app_environment.main.default_domain}"
  backend_internal_host = "backend.internal.${azurerm_container_app_environment.main.default_domain}"
  redis_internal_host   = "redis.internal.${azurerm_container_app_environment.main.default_domain}"
}

resource "azurerm_key_vault_secret" "jwt_secret" {
  name         = "jwt-secret-key"
  value        = local.jwt_secret_value
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_secrets_officer_current]
}

resource "azurerm_key_vault_secret" "mfa_key" {
  name         = "mfa-encryption-key"
  value        = var.mfa_encryption_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_secrets_officer_current]
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_secrets_officer_current]
}
