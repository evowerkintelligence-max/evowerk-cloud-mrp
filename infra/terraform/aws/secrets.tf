
# Unconditional (not count = var.jwt_secret_key == "" ? 1 : 0) on purpose -
# indexing a count-conditional resource from inside the ternary below can
# error at plan time even on the branch that isn't taken, since the
# reference chain has to resolve regardless of which branch wins. Always
# generating this is harmless; it just goes unused if jwt_secret_key was
# supplied instead.
resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

locals {
  jwt_secret_value = var.jwt_secret_key != "" ? var.jwt_secret_key : random_password.jwt_secret.result

  database_url = "postgresql+asyncpg://${var.db_username}:${random_password.db_password.result}@${aws_db_instance.postgres.endpoint}/${var.db_name}"
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name                    = "${local.name_prefix}/jwt-secret-key"
  recovery_window_in_days = 0 # throwaway test env - allow immediate re-deploy after down.sh
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = local.jwt_secret_value
}

resource "aws_secretsmanager_secret" "mfa_key" {
  name                    = "${local.name_prefix}/mfa-encryption-key"
  recovery_window_in_days = 0 # throwaway test env - allow immediate re-deploy after down.sh
}

resource "aws_secretsmanager_secret_version" "mfa_key" {
  secret_id     = aws_secretsmanager_secret.mfa_key.id
  secret_string = var.mfa_encryption_key
}

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name_prefix}/database-url"
  recovery_window_in_days = 0 # throwaway test env - allow immediate re-deploy after down.sh
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = local.database_url
}
