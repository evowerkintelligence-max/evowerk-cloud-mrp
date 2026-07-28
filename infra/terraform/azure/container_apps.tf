resource "azurerm_container_app_environment" "main" {
  name                     = "${local.name_prefix}-env"
  location                 = azurerm_resource_group.main.location
  resource_group_name      = azurerm_resource_group.main.name
  infrastructure_subnet_id = azurerm_subnet.container_apps.id

  # azurerm computes infrastructure_resource_group_name server-side and
  # doesn't persist it back into a form that matches an unset config value,
  # so every subsequent plan sees a false "-> null" diff on this ForceNew
  # attribute and wants to destroy/recreate the environment for nothing.
  lifecycle {
    ignore_changes = [infrastructure_resource_group_name]
  }
}

# Internal-only (no external ingress) - only reachable from other apps in
# this environment, same trust boundary as the security groups in the AWS
# module. Redis holds only per-IP rate-limit counters, no business data, so
# it doesn't need durability - see the main repo README.
#
# TCP ingress on Container Apps is the least-tested part of this module
# (see infra/terraform/azure/README.md) - this is the piece most likely to
# need adjusting once this actually gets applied against a real subscription.
resource "azurerm_container_app" "redis" {
  name                         = "redis"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  template {
    container {
      name   = "redis"
      image  = var.redis_image
      cpu    = var.redis_cpu
      memory = var.redis_memory
    }

    # max_replicas = 1 is correctness, not cost: each replica would be a
    # separate in-memory Redis with its own counters, so per-IP rate limits
    # would silently divide by the replica count. Losing the counters when
    # this scales to zero is fine (they're not durable anyway), but splitting
    # them across live replicas is not.
    min_replicas = 0
    max_replicas = 1
  }

  ingress {
    external_enabled = false
    target_port      = 6379
    exposed_port     = 6379
    transport        = "tcp"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

resource "azurerm_container_app" "backend" {
  name                         = "backend"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.container_apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.container_apps.id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_url.id
    identity            = azurerm_user_assigned_identity.container_apps.id
  }

  secret {
    name                = "jwt-secret-key"
    key_vault_secret_id = azurerm_key_vault_secret.jwt_secret.id
    identity            = azurerm_user_assigned_identity.container_apps.id
  }

  secret {
    name                = "mfa-encryption-key"
    key_vault_secret_id = azurerm_key_vault_secret.mfa_key.id
    identity            = azurerm_user_assigned_identity.container_apps.id
  }

  secret {
    name                = "gemini-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.gemini_api_key.id
    identity            = azurerm_user_assigned_identity.container_apps.id
  }

  template {
    container {
      name   = "backend"
      image  = "${azurerm_container_registry.main.login_server}/backend:${var.image_tag}"
      cpu    = var.backend_cpu
      memory = var.backend_memory

      # The image's own CMD is just uvicorn (see backend/Dockerfile) - same as
      # docker-compose.yml's override. Only safe as-is at max_replicas = 1;
      # move this to a separate one-off deploy step before scaling out, so
      # multiple replicas don't race to run `alembic upgrade head`. Scaling to
      # zero is fine: a cold start runs the migration exactly once, because
      # max_replicas = 1 means there is never a second replica to race with.
      command = ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

      env {
        name  = "ENV"
        value = "production"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${local.redis_internal_host}:6379/0"
      }
      env {
        name  = "CORS_ORIGINS"
        value = jsonencode([local.frontend_url])
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "JWT_SECRET_KEY"
        secret_name = "jwt-secret-key"
      }
      env {
        name        = "MFA_ENCRYPTION_KEY"
        secret_name = "mfa-encryption-key"
      }
      env {
        name        = "GEMINI_API_KEY"
        secret_name = "gemini-api-key"
      }
    }

    # ponytail: scale-to-zero, sized for a dev/test subscription funded by
    # credits - an idle backend costs nothing overnight, at the price of a
    # cold start (container pull + `alembic upgrade head`) on the first
    # request after idling. Set min_replicas = 1 for anything latency-
    # sensitive or customer-facing.
    min_replicas = 0
    max_replicas = 1
  }

  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "http"
    # Internal ingress still enforces HTTPS by default (Azure terminates TLS
    # at its own edge either way), but nginx's proxy_pass talks plain HTTP to
    # this upstream - without this, every proxied request 301s to https://
    # this internal-only hostname, which nginx doesn't follow.
    allow_insecure_connections = true

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.kv_secrets_user,
    azurerm_container_app.redis,
  ]
}

resource "azurerm_container_app" "frontend" {
  name                         = "frontend"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.container_apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.container_apps.id
  }

  template {
    container {
      name   = "frontend"
      image  = "${azurerm_container_registry.main.login_server}/frontend:${var.image_tag}"
      cpu    = var.frontend_cpu
      memory = var.frontend_memory

      # Overrides Dockerfile.prod's local-dev default (backend:8000, the
      # docker-compose service name) - no port here, Container Apps ingress
      # always fronts on 80/443 regardless of the container's actual
      # target_port, so nginx.conf.template's proxy_pass defaults correctly.
      env {
        name  = "BACKEND_UPSTREAM"
        value = local.backend_internal_host
      }

      # nginx.conf.template's `resolver` directive needs this for per-request
      # DNS re-resolution of BACKEND_UPSTREAM (see that file's comment).
      # NOT 168.63.129.16 (Azure's platform DNS): that resolves an internal
      # Container Apps hostname to the environment's PUBLIC ingress IP
      # instead of its private one, so a proxied request loops back through
      # the public edge and 404s with Azure's "stopped or does not exist"
      # page. 127.0.0.11 is the container's own embedded DNS (standard
      # Docker/containerd resolver), which resolves it correctly.
      env {
        name  = "DNS_RESOLVER"
        value = "127.0.0.11"
      }
    }

    # ponytail: pinned to 1 for a dev/test box. Raise max_replicas when real
    # traffic justifies it - nginx here is stateless, so it scales out freely.
    min_replicas = 0
    max_replicas = 1
  }

  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull, azurerm_container_app.backend]
}
