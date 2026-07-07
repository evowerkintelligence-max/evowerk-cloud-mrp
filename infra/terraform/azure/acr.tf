# ACR names must be globally unique and alphanumeric only (no hyphens) -
# hence the replace() rather than using local.name_prefix directly.
resource "azurerm_container_registry" "main" {
  name                = replace("${local.name_prefix}acr", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
}
