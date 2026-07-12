#!/usr/bin/env bash
# Bring the test deployment up: ECR repos -> build/push images -> full stack.
# ~10 min, mostly waiting on NAT gateway/ALB/RDS. RDS is destroyed by down.sh
# each time, so this always starts from a fresh empty database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

AWS="/c/Program Files/Amazon/AWSCLIV2/aws.exe"
REGION="us-east-1"

echo "== Phase 1: creating ECR repos =="
terraform apply -input=false -auto-approve \
  -target=aws_ecr_repository.backend \
  -target=aws_ecr_repository.frontend

BACKEND_URL=$(terraform output -raw ecr_backend_repository_url)
FRONTEND_URL=$(terraform output -raw ecr_frontend_repository_url)

echo "== Logging in to ECR =="
"$AWS" ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${BACKEND_URL%%/*}"

echo "== Building + pushing backend =="
docker build -t "$BACKEND_URL:latest" ../../../backend
docker push "$BACKEND_URL:latest"

echo "== Building + pushing frontend =="
docker build -t "$FRONTEND_URL:latest" -f ../../../frontend/Dockerfile.prod ../../../frontend
docker push "$FRONTEND_URL:latest"

echo "== Phase 2: creating VPC, RDS, ECS, ALB =="
terraform apply -input=false -auto-approve

echo
echo "App is up at:"
terraform output -raw app_url
echo
