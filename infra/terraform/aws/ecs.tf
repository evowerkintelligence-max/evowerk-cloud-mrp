resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"
}

# Private DNS namespace for service-to-service calls (frontend -> backend,
# backend -> redis) - the frontend's nginx.conf.template proxies to whatever
# BACKEND_UPSTREAM points at, which is set to "backend.internal:8000" below.
resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "internal"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "backend" {
  name = "backend"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_service_discovery_service" "redis" {
  name = "redis"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name_prefix}/backend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/${local.name_prefix}/frontend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "redis" {
  name              = "/ecs/${local.name_prefix}/redis"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name_prefix}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend_cpu
  memory                   = var.backend_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  volume {
    name = "data"
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.data.id
      root_directory = "/"
    }
  }

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
      essential = true
      # The image's own CMD is just uvicorn (see backend/Dockerfile) - same
      # as docker-compose.yml's override, migrations run before the server
      # starts. Only safe as-is at desired_count = 1; move this to a
      # separate one-off deploy task before scaling the backend service out,
      # so multiple tasks don't all race to run `alembic upgrade head`.
      command = ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

      portMappings = [
        { containerPort = 8000, protocol = "tcp" }
      ]

      # Backs data_dir (./data, resolved to /app/data - see backend/app/core/
      # config.py + Dockerfile's WORKDIR) with the EFS volume above, so
      # uploaded dataset files survive task restarts instead of living only
      # in the container's throwaway layer.
      mountPoints = [
        { sourceVolume = "data", containerPath = "/app/data", readOnly = false }
      ]

      environment = [
        { name = "ENV", value = "production" },
        { name = "REDIS_URL", value = "redis://redis.internal:6379/0" },
        { name = "CORS_ORIGINS", value = jsonencode([var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"]) },
        { name = "SES_FROM_EMAIL", value = var.ses_from_email },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "FRONTEND_BASE_URL", value = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}" },
        { name = "GOOGLE_OAUTH_CLIENT_ID", value = var.google_oauth_client_id },
        { name = "MICROSOFT_OAUTH_CLIENT_ID", value = var.microsoft_oauth_client_id },
        { name = "RATE_LIMIT_LOGIN_PER_5MIN", value = tostring(var.rate_limit_login_per_5min) },
        { name = "RATE_LIMIT_REFRESH_PER_5MIN", value = tostring(var.rate_limit_refresh_per_5min) },
        { name = "RATE_LIMIT_REGISTER_PER_HOUR", value = tostring(var.rate_limit_register_per_hour) },
        { name = "RATE_LIMIT_MFA_VERIFY_PER_5MIN", value = tostring(var.rate_limit_mfa_verify_per_5min) },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
        { name = "JWT_SECRET_KEY", valueFrom = aws_secretsmanager_secret.jwt_secret.arn },
        { name = "MFA_ENCRYPTION_KEY", valueFrom = aws_secretsmanager_secret.mfa_key.arn },
        { name = "GOOGLE_OAUTH_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.google_oauth_secret.arn },
        { name = "MICROSOFT_OAUTH_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.microsoft_oauth_secret.arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.backend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "backend"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name_prefix}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.frontend_cpu
  memory                   = var.frontend_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "frontend"
      image     = "${aws_ecr_repository.frontend.repository_url}:${var.image_tag}"
      essential = true

      portMappings = [
        { containerPort = 80, protocol = "tcp" }
      ]

      # Overrides Dockerfile.prod's local-dev default (backend:8000, the
      # docker-compose service name) with this deployment's Cloud Map name.
      # DNS_RESOLVER (the VPC's Amazon-provided DNS server, base CIDR + 2) is
      # required so nginx re-resolves backend.internal per request instead of
      # caching whatever IP it got at container startup forever - otherwise
      # replacing the backend task (new ENI/IP) leaves the frontend silently
      # proxying to a dead address until the frontend container itself
      # restarts. See nginx.conf.template's `resolver` + `set $backend_upstream`.
      environment = [
        { name = "BACKEND_UPSTREAM", value = "backend.internal:8000" },
        { name = "DNS_RESOLVER", value = cidrhost(var.vpc_cidr, 2) },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.frontend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "frontend"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "redis" {
  family                   = "${local.name_prefix}-redis"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.redis_cpu
  memory                   = var.redis_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "redis"
      image     = var.redis_image
      essential = true

      portMappings = [
        { containerPort = 6379, protocol = "tcp" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.redis.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "redis"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "redis" {
  name            = "redis"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.redis.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.redis.id]
  }

  service_registries {
    registry_arn = aws_service_discovery_service.redis.arn
  }
}

resource "aws_ecs_service" "backend" {
  name            = "backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  # ponytail: exec access for one-off ops (staff-account seeding etc), turn
  # off once ECS Exec isn't needed day-to-day.
  enable_execute_command = true

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.backend.id]
  }

  service_registries {
    registry_arn = aws_service_discovery_service.backend.arn
  }

  depends_on = [aws_ecs_service.redis, aws_db_instance.postgres, aws_efs_mount_target.data]
}

resource "aws_ecs_service" "frontend" {
  name            = "frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.frontend.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 80
  }

  depends_on = [aws_lb_listener.https, aws_lb_listener.http_forward, aws_ecs_service.backend]
}
