#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GCP_INFRA_DIR="$ROOT_DIR/infra/gcp"
FRONTEND_DIR="$ROOT_DIR/frontend"

cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "Missing .env in $ROOT_DIR" >&2
  exit 1
fi

set -a
source ".env"
set +a

required_vars=(
  GCP_PROJECT_ID
  GCP_REGION
  FIREBASE_PROJECT_ID
  CLERK_JWKS_URL
  CLERK_SECRET_KEY
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
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

for command_name in terraform gcloud npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

if command -v firebase >/dev/null 2>&1; then
  FIREBASE_BIN="firebase"
elif [[ -x "$FRONTEND_DIR/node_modules/.bin/firebase" ]]; then
  FIREBASE_BIN="$FRONTEND_DIR/node_modules/.bin/firebase"
else
  echo "Missing required command: firebase" >&2
  echo "Install it with: npm --prefix frontend install --save-dev firebase-tools" >&2
  exit 1
fi

export TF_VAR_project_id="$GCP_PROJECT_ID"
export TF_VAR_region="$GCP_REGION"
export TF_VAR_firebase_project_id="$FIREBASE_PROJECT_ID"
export TF_VAR_firebase_site_id="$FIREBASE_PROJECT_ID"
export TF_VAR_clerk_jwks_url="$CLERK_JWKS_URL"
export TF_VAR_clerk_secret_key="$CLERK_SECRET_KEY"
export TF_VAR_sendgrid_api_key="$SENDGRID_API_KEY"
export TF_VAR_sendgrid_sender_email="$SENDGRID_SENDER_EMAIL"
export TF_VAR_pushover_token="$PUSHOVER_TOKEN"
export TF_VAR_pushover_user="$PUSHOVER_USER"
DEPLOY_VERSION="${DEPLOY_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
DEPLOY_VERSION="${DEPLOY_VERSION}-$(date +%Y%m%d%H%M%S)"
export TF_VAR_app_version="$DEPLOY_VERSION"
export TF_VAR_backend_image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/medinotes/medinotes-backend:${DEPLOY_VERSION}"

echo "Using GCP project: $GCP_PROJECT_ID"
echo "Using Firebase project/site: $FIREBASE_PROJECT_ID"
echo "Using region: $GCP_REGION"
echo "Using backend version: $DEPLOY_VERSION"

gcloud config set project "$GCP_PROJECT_ID" >/dev/null

echo "Bootstrapping GCP APIs and Artifact Registry..."
terraform -chdir="$GCP_INFRA_DIR" init

if ! terraform -chdir="$GCP_INFRA_DIR" state show google_artifact_registry_repository.backend >/dev/null 2>&1; then
  if gcloud artifacts repositories describe medinotes \
    --project "$GCP_PROJECT_ID" \
    --location "$GCP_REGION" >/dev/null 2>&1; then
    echo "Importing existing Artifact Registry repository into Terraform state..."
    terraform -chdir="$GCP_INFRA_DIR" import \
      google_artifact_registry_repository.backend \
      "projects/${GCP_PROJECT_ID}/locations/${GCP_REGION}/repositories/medinotes"
  fi
fi

terraform -chdir="$GCP_INFRA_DIR" apply \
  -target=google_project_service.gcp_apis \
  -target=google_artifact_registry_repository.backend \
  -auto-approve

RUNTIME_SECRET_IMPORTS=(
  "CLERK_SECRET_KEY:clerk-secret-key"
  "SENDGRID_API_KEY:sendgrid-api-key"
  "SENDGRID_SENDER_EMAIL:sendgrid-sender-email"
  "PUSHOVER_TOKEN:pushover-token"
  "PUSHOVER_USER:pushover-user"
)

for secret_import in "${RUNTIME_SECRET_IMPORTS[@]}"; do
  secret_key="${secret_import%%:*}"
  secret_id="${secret_import#*:}"
  resource_address="google_secret_manager_secret.runtime[\"${secret_key}\"]"

  if ! terraform -chdir="$GCP_INFRA_DIR" state show "$resource_address" >/dev/null 2>&1; then
    if gcloud secrets describe "$secret_id" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
      echo "Importing existing Secret Manager secret into Terraform state: $secret_id"
      terraform -chdir="$GCP_INFRA_DIR" import \
        "$resource_address" \
        "projects/${GCP_PROJECT_ID}/secrets/${secret_id}"
    fi
  fi
done

echo "Building and pushing backend image..."
gcloud builds submit \
  --config "$GCP_INFRA_DIR/cloudbuild.backend.yaml" \
  --substitutions "_IMAGE=$TF_VAR_backend_image" \
  "$ROOT_DIR"

echo "Applying full GCP Terraform stack..."
terraform -chdir="$GCP_INFRA_DIR" apply -auto-approve

BACKEND_URL="$(terraform -chdir="$GCP_INFRA_DIR" output -raw backend_url)"

echo "Building Firebase Hosting bundle..."
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  npm --prefix "$FRONTEND_DIR" ci
fi

NEXT_PUBLIC_API_BASE_URL="$BACKEND_URL" \
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" \
npm --prefix "$FRONTEND_DIR" run build

echo "Deploying Firebase Hosting..."
(cd "$FRONTEND_DIR" && "$FIREBASE_BIN" deploy --only hosting --project "$FIREBASE_PROJECT_ID")

echo "Deployment complete."
echo "Backend: $BACKEND_URL"
echo "Frontend: https://${FIREBASE_PROJECT_ID}.web.app"
