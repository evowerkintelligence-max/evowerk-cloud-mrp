# Alarms on the signals that mean the app is down or about to be: ALB 5xx and
# unhealthy targets, backend CPU/memory saturation, RDS CPU and disk. Every
# alarm (and its recovery) goes to one SNS topic; set alarm_email to get it
# by email, or subscribe a pager/Slack endpoint to the topic yourself.
data "aws_caller_identity" "current" {}

# Customer-managed key, not alias/aws/sns: CloudWatch can't publish to a topic
# under the AWS-managed key (its policy can't grant cloudwatch.amazonaws.com),
# so alarms would fire and silently never notify anyone.
resource "aws_kms_key" "alarms" {
  description         = "${local.name_prefix} alarm SNS topic"
  enable_key_rotation = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
      },
    ]
  })
}

resource "aws_sns_topic" "alarms" {
  name              = "${local.name_prefix}-alarms"
  kms_master_key_id = aws_kms_key.alarms.arn
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alb_dims     = { LoadBalancer = aws_lb.main.arn_suffix }
  backend_dims = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.backend.name }
  rds_dims     = { DBInstanceIdentifier = aws_db_instance.postgres.identifier }

  alarms = {
    alb-target-5xx = {
      namespace = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", stat = "Sum"
      dims      = local.alb_dims, op = "GreaterThanThreshold", threshold = 10
      desc      = "Frontend/backend returned more than 10 5xx responses in 5 minutes."
    }
    alb-elb-5xx = {
      namespace = "AWS/ApplicationELB", metric = "HTTPCode_ELB_5XX_Count", stat = "Sum"
      dims      = local.alb_dims, op = "GreaterThanThreshold", threshold = 10
      desc      = "The ALB itself returned more than 10 5xx responses in 5 minutes (no healthy target, timeouts)."
    }
    alb-unhealthy-hosts = {
      namespace = "AWS/ApplicationELB", metric = "UnHealthyHostCount", stat = "Maximum"
      dims      = merge(local.alb_dims, { TargetGroup = aws_lb_target_group.frontend.arn_suffix })
      op        = "GreaterThanThreshold", threshold = 0
      desc      = "A frontend task is failing its ALB health check."
    }
    backend-cpu = {
      namespace = "AWS/ECS", metric = "CPUUtilization", stat = "Average"
      dims      = local.backend_dims, op = "GreaterThanThreshold", threshold = 85
      desc      = "Backend CPU above 85% for 5 minutes: raise backend_cpu."
    }
    backend-memory = {
      namespace = "AWS/ECS", metric = "MemoryUtilization", stat = "Average"
      dims      = local.backend_dims, op = "GreaterThanThreshold", threshold = 85
      desc      = "Backend memory above 85% for 5 minutes: raise backend_memory before it OOMs."
    }
    rds-cpu = {
      namespace = "AWS/RDS", metric = "CPUUtilization", stat = "Average"
      dims      = local.rds_dims, op = "GreaterThanThreshold", threshold = 80
      desc      = "Postgres CPU above 80% for 5 minutes: raise db_instance_class."
    }
    rds-free-storage = {
      namespace = "AWS/RDS", metric = "FreeStorageSpace", stat = "Minimum"
      dims      = local.rds_dims, op = "LessThanThreshold", threshold = 2 * 1024 * 1024 * 1024
      desc      = "Postgres has under 2 GiB free: raise db_allocated_storage."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${local.name_prefix}-${each.key}"
  alarm_description   = each.value.desc
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dims
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  period              = 300
  evaluation_periods  = 1
  # No datapoints = no traffic/no errors, not an outage.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alarms.arn]
  ok_actions         = [aws_sns_topic.alarms.arn]
}
