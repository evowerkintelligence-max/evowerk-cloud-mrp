locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "${local.name_prefix}-rg"
  location = var.azure_location
}
