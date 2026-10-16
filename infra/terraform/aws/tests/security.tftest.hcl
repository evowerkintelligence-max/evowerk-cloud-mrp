# Plan-only tests against a mocked AWS provider: no credentials, no real
# resources. They pin the security invariants of this config so a refactor
# can't silently drop them. Run with `terraform test` (Terraform >= 1.7).

mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = {
      names = ["us-east-1a", "us-east-1b"]
    }
  }

  # The provider validates role/policy JSON even when mocked.
  override_data {
    target = data.aws_iam_policy_document.ecs_assume
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.secrets_read
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.ecs_exec
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  # The defaults ("evowerk-mrp-production") push the target group name past
  # AWS's 32-char limit, so tests use a short environment name.
  environment = "test"
  # Shape-valid dummy Fernet key, not a real secret.
  mfa_encryption_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
}

run "database_is_private_and_encrypted" {
  command = plan

  assert {
    condition     = aws_db_instance.postgres.storage_encrypted
    error_message = "RDS storage must be encrypted at rest."
  }

  assert {
    condition     = !aws_db_instance.postgres.publicly_accessible
    error_message = "RDS must not be publicly accessible."
  }

  assert {
    condition     = aws_db_instance.postgres.deletion_protection && !aws_db_instance.postgres.skip_final_snapshot
    error_message = "RDS must default to deletion protection and a final snapshot."
  }

  assert {
    condition     = alltrue([for r in aws_security_group.rds.ingress : try(length(r.cidr_blocks), 0) == 0 && r.from_port == 5432])
    error_message = "RDS ingress must be Postgres from security groups only, never from a CIDR."
  }
}

run "data_at_rest_and_in_transit_encrypted" {
  command = plan

  assert {
    condition     = aws_efs_file_system.data.encrypted
    error_message = "EFS data volume must be encrypted at rest."
  }

  assert {
    condition     = one(aws_ecs_task_definition.backend.volume).efs_volume_configuration[0].transit_encryption == "ENABLED"
    error_message = "Backend EFS mount must use TLS in transit."
  }
}

run "only_the_alb_is_internet_facing" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_security_group.alb.ingress : contains([80, 443], r.from_port) && r.from_port == r.to_port])
    error_message = "ALB may only accept ports 80 and 443."
  }

  assert {
    condition = alltrue(flatten([
      for sg in [aws_security_group.frontend, aws_security_group.backend, aws_security_group.redis, aws_security_group.efs] :
      [for r in sg.ingress : try(length(r.cidr_blocks), 0) == 0]
    ]))
    error_message = "Task, Redis and EFS security groups must only accept traffic from other security groups."
  }

  assert {
    condition     = aws_lb.main.drop_invalid_header_fields
    error_message = "ALB must drop invalid HTTP header fields."
  }

  assert {
    condition     = alltrue([for s in [aws_ecs_service.frontend, aws_ecs_service.backend, aws_ecs_service.redis] : !coalesce(s.network_configuration[0].assign_public_ip, false)])
    error_message = "ECS services must not get public IPs."
  }
}

run "container_images_are_immutable_and_scanned" {
  command = plan

  assert {
    condition     = alltrue([for r in [aws_ecr_repository.backend, aws_ecr_repository.frontend] : r.image_tag_mutability == "IMMUTABLE" && r.image_scanning_configuration[0].scan_on_push])
    error_message = "ECR repos must use immutable tags and scan on push."
  }
}

run "https_enforced_when_domain_set" {
  command = plan

  variables {
    domain_name = "mrp.example.com"
  }

  assert {
    condition     = aws_lb_listener.https[0].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    error_message = "HTTPS listener must use the TLS 1.2/1.3 policy."
  }

  assert {
    condition     = one(aws_lb_listener.http_redirect[0].default_action).redirect[0].protocol == "HTTPS"
    error_message = "Port 80 must redirect to HTTPS when a domain is set."
  }

  assert {
    condition     = length(aws_lb_listener.http_forward) == 0
    error_message = "The plain-HTTP forward listener must not exist when a domain is set."
  }
}

run "rejects_invalid_mfa_key" {
  command = plan

  variables {
    mfa_encryption_key = "REPLACE_ME"
  }

  expect_failures = [var.mfa_encryption_key]
}

run "rejects_invalid_vpc_cidr" {
  command = plan

  variables {
    vpc_cidr = "10.20.0.0/28"
  }

  expect_failures = [var.vpc_cidr]
}

run "rejects_uppercase_environment" {
  command = plan

  variables {
    environment = "Prod"
  }

  expect_failures = [var.environment]
}

run "rejects_invalid_fargate_cpu" {
  command = plan

  variables {
    backend_cpu = 1000
  }

  expect_failures = [var.backend_cpu]
}
