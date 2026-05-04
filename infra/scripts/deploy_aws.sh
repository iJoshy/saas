#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AWS_INFRA_DIR="$ROOT_DIR/infra/aws"

cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "Missing .env in $ROOT_DIR" >&2
  exit 1
fi

set -a
source ".env"
set +a

required_vars=(
  AWS_ACCOUNT_ID
  DEFAULT_AWS_REGION
  CLERK_JWKS_URL
  CLERK_SECRET_KEY
  GEMINI_API_KEY
  SENDGRID_API_KEY
  SENDGRID_SENDER_EMAIL
  PUSHOVER_TOKEN
  PUSHOVER_USER
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required .env value: $var_name" >&2
    exit 1
  fi
done

for command_name in terraform aws docker; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

APP_NAME="${APP_NAME:-medinotes}"
ECR_REPOSITORY="${APP_NAME}-backend"
BACKEND_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${DEFAULT_AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:latest"

export AWS_REGION="$DEFAULT_AWS_REGION"
export TF_VAR_region="$DEFAULT_AWS_REGION"
export TF_VAR_app_name="$APP_NAME"
export TF_VAR_backend_image="$BACKEND_IMAGE"
export TF_VAR_clerk_jwks_url="$CLERK_JWKS_URL"
export TF_VAR_clerk_secret_key="$CLERK_SECRET_KEY"
export TF_VAR_gemini_api_key="$GEMINI_API_KEY"
export TF_VAR_sendgrid_api_key="$SENDGRID_API_KEY"
export TF_VAR_sendgrid_sender_email="$SENDGRID_SENDER_EMAIL"
export TF_VAR_pushover_token="$PUSHOVER_TOKEN"
export TF_VAR_pushover_user="$PUSHOVER_USER"

echo "Using AWS account: $AWS_ACCOUNT_ID"
echo "Using AWS region: $DEFAULT_AWS_REGION"
echo "Using App Runner image: $BACKEND_IMAGE"

echo "Bootstrapping ECR..."
terraform -chdir="$AWS_INFRA_DIR" init
terraform -chdir="$AWS_INFRA_DIR" apply \
  -target=aws_ecr_repository.backend \
  -auto-approve

echo "Building and pushing backend image..."
aws ecr get-login-password --region "$DEFAULT_AWS_REGION" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${DEFAULT_AWS_REGION}.amazonaws.com"
docker build -f backend/Dockerfile -t "$BACKEND_IMAGE" .
docker push "$BACKEND_IMAGE"

echo "Applying full AWS Terraform stack..."
terraform -chdir="$AWS_INFRA_DIR" apply -auto-approve

BACKEND_URL="$(terraform -chdir="$AWS_INFRA_DIR" output -raw backend_url)"

echo "Deployment complete."
echo "Backend: $BACKEND_URL"
