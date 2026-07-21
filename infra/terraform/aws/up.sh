#!/usr/bin/env bash
# Bring the test deployment up: ECR repos -> build/push images -> full stack.
# ~10 min, mostly waiting on NAT gateway/ALB/RDS. RDS is destroyed by down.sh
# each time, so this always starts from a fresh empty database. ECR repos
# persist across up/down cycles (see down.sh) so unchanged image layers are
# reused instead of re-uploaded every time.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

AWS="/c/Program Files/Amazon/AWSCLIV2/aws.exe"
REGION="us-east-1"
# Repos are IMMUTABLE (see ecr.tf), and now persist across runs, so a fixed
# ":latest" tag would collide with whatever a previous run already pushed.
# A fresh tag per run keeps that safety property without re-uploading
# content: blob layers are still deduped by digest regardless of tag.
TAG="$(git rev-parse --short HEAD 2>/dev/null || echo local)-$(date +%s)"

echo "== Phase 1: creating ECR repos =="
terraform apply -input=false -auto-approve \
  -target=aws_ecr_repository.backend \
  -target=aws_ecr_repository.frontend

BACKEND_URL=$(terraform output -raw ecr_backend_repository_url)
FRONTEND_URL=$(terraform output -raw ecr_frontend_repository_url)

echo "== Logging in to ECR =="
"$AWS" ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${BACKEND_URL%%/*}"

echo "== Building + pushing backend ($TAG) =="
docker build -t "$BACKEND_URL:$TAG" ../../../backend
docker push "$BACKEND_URL:$TAG"

echo "== Building + pushing frontend ($TAG) =="
docker build -t "$FRONTEND_URL:$TAG" -f ../../../frontend/Dockerfile.prod ../../../frontend
docker push "$FRONTEND_URL:$TAG"

echo "== Phase 2: creating VPC, RDS, ECS, ALB =="
terraform apply -input=false -auto-approve -var "image_tag=$TAG"

echo
echo "App is up at:"
terraform output -raw app_url
echo
