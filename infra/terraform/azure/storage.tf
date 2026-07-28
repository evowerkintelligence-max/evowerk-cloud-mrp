# Persistent storage for the backend's data_dir (app/core/config.py), which
# holds uploaded dataset files and AutoML run artifacts. Without this the
# files live on the container's throwaway layer, so every new revision,
# restart, or scale-to-zero loses them while the Postgres rows describing
# them survive - the app then 404s with "Dataset file is missing on disk".
# This is the Azure equivalent of docker-compose.yml's backend_data volume.
#
# Storage account names must be globally unique, lowercase alphanumeric, and
# <= 24 chars - hence the replace(), same as acr.tf.
resource "azurerm_storage_account" "data" {
  name                            = replace("${local.name_prefix}data", "-", "")
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  # ponytail: reachable over its public endpoint, because Container Apps
  # mounts the share over SMB with the account key rather than through the
  # VNet. Access still requires that key, which only the Container Apps
  # environment holds. Move to a private endpoint + private DNS zone if
  # customer datasets need the same network isolation as Postgres.
}

resource "azurerm_storage_share" "backend_data" {
  name                 = "backend-data"
  storage_account_name = azurerm_storage_account.data.name
  quota                = var.dataset_storage_quota_gb
}

resource "azurerm_container_app_environment_storage" "backend_data" {
  name                         = "backend-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.data.name
  share_name                   = azurerm_storage_share.backend_data.name
  access_key                   = azurerm_storage_account.data.primary_access_key
  access_mode                  = "ReadWrite"
}
