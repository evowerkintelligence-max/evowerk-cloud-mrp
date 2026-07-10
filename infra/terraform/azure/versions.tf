terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # No backend configured here on purpose - see infra/terraform/aws/README.md's
  # state warning, which applies equally here (this state holds the DB
  # password and JWT secret in plaintext).
}

provider "azurerm" {
  features {
    key_vault {
      # false on purpose: a destroy/replace only soft-deletes the vault, leaving
      # it recoverable for its retention window instead of permanently wiping
      # the JWT secret, DB password, and MFA key with no way back.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}
