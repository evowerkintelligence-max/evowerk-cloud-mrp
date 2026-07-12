#!/usr/bin/env bash
# Tear the test deployment down completely so it stops billing. Empties both
# ECR repos first since terraform destroy refuses to delete a repo that
# still has images in it (image_tag_mutability = IMMUTABLE means no
# force_delete on the resource itself - see ecr.tf).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

AWS="/c/Program Files/Amazon/AWSCLIV2/aws.exe"
REGION="us-east-1"

for repo_var in ecr_backend_repository_url ecr_frontend_repository_url; do
  url=$(terraform output -raw "$repo_var" 2>/dev/null || true)
  [ -z "$url" ] && continue
  repo="${url##*/}"
  digests=$("$AWS" ecr list-images --repository-name "$repo" --region "$REGION" --query 'imageIds[].imageDigest' --output text 2>/dev/null || true)
  if [ -n "$digests" ]; then
    ids=()
    for d in $digests; do ids+=(imageDigest="$d"); done
    echo "== Emptying $repo =="
    "$AWS" ecr batch-delete-image --repository-name "$repo" --region "$REGION" --image-ids "${ids[@]}" >/dev/null
  fi
done

echo "== Destroying all infrastructure =="
terraform destroy -input=false -auto-approve

echo "Done - all billed resources destroyed."
