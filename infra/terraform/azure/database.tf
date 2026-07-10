resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name_prefix}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.name_prefix}-postgres-link"
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
  resource_group_name   = azurerm_resource_group.main.name
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "${local.name_prefix}-db"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  version = "16"
  # Azure encrypts Flexible Server storage at rest by default (Microsoft-
  # managed keys) - resolves the security roadmap's "encryption at rest"
  # requirement with no extra configuration needed here.
  sku_name   = var.db_sku_name
  storage_mb = var.db_storage_mb

  administrator_login    = var.db_username
  administrator_password = random_password.db_password.result

  delegated_subnet_id = azurerm_subnet.postgres.id
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id

  backup_retention_days = 7

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  # azurerm_postgresql_flexible_server has no native deletion_protection
  # argument like aws_db_instance does - this is the Terraform-level
  # equivalent so both deploy targets require the same deliberate step
  # (removing this block) before the DB can be destroyed.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}
