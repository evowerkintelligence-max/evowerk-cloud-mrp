data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The managed policy above covers ECR pull + CloudWatch Logs, but not reading
# the secrets referenced in the task definitions - that needs its own grant.
data "aws_iam_policy_document" "secrets_read" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.jwt_secret.arn,
      aws_secretsmanager_secret.mfa_key.arn,
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.google_oauth_secret.arn,
      aws_secretsmanager_secret.microsoft_oauth_secret.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "${local.name_prefix}-secrets-read"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

# Task role (what the running application itself can do, as opposed to the
# execution role above which is ECS's own bootstrapping). Empty for now - the
# app doesn't call any AWS APIs from inside its own code yet. Attach policies
# here if/when a feature needs to (e.g. S3 access for file uploads).
resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "ses_send" {
  count = var.ses_from_email != "" ? 1 : 0
  statement {
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_ses" {
  count  = var.ses_from_email != "" ? 1 : 0
  name   = "${local.name_prefix}-ses-send"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ses_send[0].json
}
