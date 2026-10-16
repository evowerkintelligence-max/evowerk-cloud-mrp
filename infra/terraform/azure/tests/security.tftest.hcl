# Plan-only tests against a mocked azurerm provider: no credentials, no real
# resources. They pin the security invariants of this config so a refactor
# can't silently drop them. Run with `terraform test` (Terraform >= 1.7).

mock_provider "azurerm" {
  override_data {
    target = data.azurerm_client_config.current
    values = {
      tenant_id = "00000000-0000-0000-0000-000000000000"
      object_id = "00000000-0000-0000-0000-000000000001"
    }
  }
}

variables {
  # Shape-valid dummy values, not real secrets.
  mfa_encryption_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  gemini_api_key     = "test-key" # pragma: allowlist secret
}

run "database_is_private" {
  command = plan

  assert {
    condition     = !azurerm_postgresql_flexible_server.main.public_network_access_enabled
    error_message = "Postgres must not be reachable over a public endpoint."
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.main.backup_retention_days >= 7
    error_message = "Postgres must keep at least 7 days of backups."
  }
}

run "storage_blocks_public_access" {
  command = plan

  assert {
    condition     = !azurerm_storage_account.data.allow_nested_items_to_be_public
    error_message = "Storage account must not allow public blob access."
  }

  assert {
    condition     = azurerm_storage_account.data.min_tls_version == "TLS1_2"
    error_message = "Storage account must require TLS 1.2."
  }
}

run "secrets_come_from_key_vault" {
  command = plan

  assert {
    condition     = azurerm_key_vault.main.enable_rbac_authorization
    error_message = "Key Vault must use RBAC, not access policies."
  }

  assert {
    condition     = alltrue([for s in azurerm_container_app.backend.secret : s.value == null])
    error_message = "Backend secrets must be Key Vault references, not inline values."
  }

  assert {
    condition = alltrue([
      for e in azurerm_container_app.backend.template[0].container[0].env :
      e.value == null if contains(["DATABASE_URL", "JWT_SECRET_KEY", "MFA_ENCRYPTION_KEY", "GEMINI_API_KEY"], e.name)
    ])
    error_message = "Secret env vars must be wired through secret_name, never a plain value."
  }
}

run "only_the_frontend_is_external" {
  command = plan

  assert {
    condition     = azurerm_container_app.frontend.ingress[0].external_enabled
    error_message = "Frontend must be the public entry point."
  }

  assert {
    condition     = !azurerm_container_app.backend.ingress[0].external_enabled && !azurerm_container_app.redis.ingress[0].external_enabled
    error_message = "Backend and Redis must be internal-only."
  }
}

run "rejects_invalid_mfa_key" {
  command = plan

  variables {
    mfa_encryption_key = "REPLACE_ME"
  }

  expect_failures = [var.mfa_encryption_key]
}

run "rejects_uppercase_environment" {
  command = plan

  variables {
    environment = "Prod"
  }

  expect_failures = [var.environment]
}

run "rejects_invalid_vnet_cidr" {
  command = plan

  variables {
    vnet_cidr = "not-a-cidr"
  }

  expect_failures = [var.vnet_cidr]
}
