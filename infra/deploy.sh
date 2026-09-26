#!/usr/bin/env bash
# Deploy the Gridlock alert service: build and push the image, apply Terraform, seed the state bucket with the
# current data, and point the app (data/alerts.json) at the new API.
#
#   AWS_PROFILE=<profile> GRIDLOCK_ACCOUNT_ID=<12 digits> infra/deploy.sh
#
# Needs: aws CLI v2, docker (running), terraform >= 1.6, python3. Optional: MAINTAINER_EMAIL, BUDGET_EMAIL, APP_URL,
# EXTRA_ORIGIN (a hosted site's origin, e.g. https://gridlock.example.org).
set -euo pipefail
: "${AWS_PROFILE:?set AWS_PROFILE to the profile for the account to deploy into}"
: "${GRIDLOCK_ACCOUNT_ID:?set GRIDLOCK_ACCOUNT_ID to the 12-digit ID of the account to deploy into}"
REGION=${AWS_REGION:-us-east-1}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)

actual=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text)
if [ "$actual" != "$GRIDLOCK_ACCOUNT_ID" ]; then
  echo "Profile $AWS_PROFILE is account $actual, not $GRIDLOCK_ACCOUNT_ID. Stopping." >&2
  exit 1
fi
echo "Deploying to account $actual in $REGION with profile $AWS_PROFILE"

origins='["http://127.0.0.1:8000","http://localhost:8000"'
if [ -n "${EXTRA_ORIGIN:-}" ]; then origins+=",\"$EXTRA_ORIGIN\""; fi
origins+=']'
tfvars=(-var "aws_profile=$AWS_PROFILE" -var "account_id=$GRIDLOCK_ACCOUNT_ID" -var "region=$REGION"
        -var "maintainer_email=${MAINTAINER_EMAIL:-}" -var "budget_email=${BUDGET_EMAIL:-}" -var "app_url=${APP_URL:-}"
        -var "allowed_origins=$origins")

cd "$HERE"
terraform init -input=false
# The functions need an image before they can exist, so the registry comes first.
terraform apply -input=false -auto-approve "${tfvars[@]}" -target=aws_ecr_repository.app -target=aws_ecr_lifecycle_policy.app
repo=$(terraform output -raw ecr_repository_url)
tag="$(git -C "$ROOT" rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"

aws ecr get-login-password --profile "$AWS_PROFILE" --region "$REGION" | docker login --username AWS --password-stdin "${repo%%/*}"
docker build --platform linux/amd64 --provenance=false -f "$ROOT/backend/Dockerfile" -t "$repo:$tag" "$ROOT"
docker push "$repo:$tag"

terraform apply -input=false -auto-approve "${tfvars[@]}" -var "image_tag=$tag"
bucket=$(terraform output -raw bucket)
api=$(terraform output -raw api_url)

# Seed the state the worker and API read. The registry is only written the first time, so a later deploy
# never rolls back filings the worker has already found.
s3="aws s3 --profile $AWS_PROFILE --region $REGION"
if ! $s3 ls "s3://$bucket/state/filings.json" >/dev/null 2>&1; then
  $s3 cp "$ROOT/data/filings.json" "s3://$bucket/state/filings.json"
  $s3 cp "$ROOT/data/changes.json" "s3://$bucket/state/changes.json"
  $s3 cp "$ROOT/data/projects.json" "s3://$bucket/state/projects.json"
  $s3 sync "$ROOT/data/cache" "s3://$bucket/state/cache"
  $s3 sync "$ROOT/data/env" "s3://$bucket/state/env"
fi
$s3 sync "$ROOT/data/raw" "s3://$bucket/raw" --exclude "*" --include "*.pdf"

python3 - "$api" "$ROOT/data/alerts.json" <<'PY'
import json, sys
api, path = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
cfg["apiUrl"] = api if api.endswith("/") else api + "/"
open(path, "w").write(json.dumps(cfg) + "\n")
print("data/alerts.json now points at", cfg["apiUrl"])
PY
echo "Done. Sign up from the Changes tab, confirm the AWS Notifications email, then send yourself a sample."
