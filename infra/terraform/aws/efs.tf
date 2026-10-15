# Dataset uploads, AutoML run artifacts, and integration files all live on
# the backend's local filesystem under data_dir (see backend/app/services/
# storage.py, automl_storage.py, integrations.py, realtime.py). Fargate task
# storage is ephemeral - it's wiped on every redeploy, OOM kill, or task
# replacement - so without a durable volume mounted at that path, uploaded
# datasets vanish the moment the backend task recycles even though the
# Postgres row describing them survives. EFS gives that path a backing store
# that outlives any single task.
#
# ponytail: storage.py's docstring already earmarks S3 as the eventual
# backend ("swapping in S3/object storage later means replacing the bodies
# of these five functions, nothing above them"), which would also solve
# multi-task write-coordination for free. EFS covers all four data_dir
# writers today with one mount instead of four separate S3 migrations.
# Move to S3 when the backend service needs desired_count > 1 - a single
# NFS mount has no write-coordination story across concurrent tasks.

resource "aws_security_group" "efs" {
  name_prefix = "${local.name_prefix}-efs-"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "NFS from the backend only"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }

  # trivy:ignore:AVD-AWS-0104 Tasks need outbound HTTPS (ECR pulls, Secrets Manager, SES, Gemini); SGs are stateful.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-efs-sg" }
}

resource "aws_efs_file_system" "data" {
  encrypted = true
  tags      = { Name = "${local.name_prefix}-data" }
}

resource "aws_efs_mount_target" "data" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}
