#!/usr/bin/env bash
# Tear the test deployment down, but keep the ECR repos (and their pushed
# image layers) around: this Terraform version has no `terraform destroy
# -exclude`, so we build the -target list dynamically from everything in
# state except the ECR resources. Next up.sh then reuses unchanged layers
# instead of re-uploading the whole image from scratch. ECR storage is
# ~$0.10/GB-month - negligible next to the NAT gateway/RDS/ALB/Fargate this
# actually destroys.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

mapfile -t targets < <(terraform state list | grep -vE '^aws_ecr_(repository|lifecycle_policy)\.')

if [ ${#targets[@]} -eq 0 ]; then
  echo "Nothing to destroy (state is empty or only has ECR repos)."
  exit 0
fi

target_args=()
for t in "${targets[@]}"; do
  target_args+=(-target="$t")
done

echo "== Destroying infrastructure (ECR repos kept) =="
terraform destroy -input=false -auto-approve "${target_args[@]}"

echo "Done - billed resources destroyed. ECR repos/images kept for next up.sh."
