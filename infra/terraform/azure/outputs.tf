output "frontend_url" {
  description = "The app's HTTPS URL - Container Apps' default *.azurecontainerapps.io domain gets automatic HTTPS with zero setup, no custom domain needed to start."
  value       = local.frontend_url
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "acr_backend_repository" {
  value = "${azurerm_container_registry.main.login_server}/backend"
}

output "acr_frontend_repository" {
  value = "${azurerm_container_registry.main.login_server}/frontend"
}

output "postgres_fqdn" {
  value     = azurerm_postgresql_flexible_server.main.fqdn
  sensitive = true
}

output "db_password" {
  description = "Only needed if you need to connect manually - the app itself reads DATABASE_URL from Key Vault"
  value       = random_password.db_password.result
  sensitive   = true
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}
