# Metric alerts on the signals that mean the app is down or about to be:
# frontend 5xx, backend restarts (crash loop / OOM), Postgres CPU and disk.
# All go to one action group; set alarm_email to get them by email, or add
# another receiver to the action group yourself.
resource "azurerm_monitor_action_group" "alarms" {
  name                = "${local.name_prefix}-alarms"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "evowerk"

  dynamic "email_receiver" {
    for_each = var.alarm_email == "" ? [] : [var.alarm_email]
    content {
      name                    = "alarm-email"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

locals {
  alerts = {
    frontend-5xx = {
      scope  = azurerm_container_app.frontend.id, namespace = "Microsoft.App/containerApps"
      metric = "Requests", agg = "Total", op = "GreaterThan", threshold = 10
      dim    = { name = "statusCodeCategory", values = ["5xx"] }
      desc   = "Frontend returned more than 10 5xx responses in 5 minutes."
    }
    backend-restarts = {
      scope  = azurerm_container_app.backend.id, namespace = "Microsoft.App/containerApps"
      metric = "RestartCount", agg = "Maximum", op = "GreaterThan", threshold = 0
      dim    = null
      desc   = "Backend container restarted (crash loop or OOM)."
    }
    postgres-cpu = {
      scope  = azurerm_postgresql_flexible_server.main.id, namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
      metric = "cpu_percent", agg = "Average", op = "GreaterThan", threshold = 80
      dim    = null
      desc   = "Postgres CPU above 80% for 5 minutes: raise db_sku_name."
    }
    postgres-storage = {
      scope  = azurerm_postgresql_flexible_server.main.id, namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
      metric = "storage_percent", agg = "Average", op = "GreaterThan", threshold = 85
      dim    = null
      desc   = "Postgres storage above 85%: raise db_storage_mb."
    }
  }
}

resource "azurerm_monitor_metric_alert" "this" {
  for_each = local.alerts

  name                = "${local.name_prefix}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [each.value.scope]
  description         = each.value.desc
  frequency           = "PT1M"
  window_size         = "PT5M"
  severity            = 2

  criteria {
    metric_namespace = each.value.namespace
    metric_name      = each.value.metric
    aggregation      = each.value.agg
    operator         = each.value.op
    threshold        = each.value.threshold

    dynamic "dimension" {
      for_each = each.value.dim == null ? [] : [each.value.dim]
      content {
        name     = dimension.value.name
        operator = "Include"
        values   = dimension.value.values
      }
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.alarms.id
  }
}
