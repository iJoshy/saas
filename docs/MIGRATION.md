# MediNotes AWS to GCP Migration Plan

## Target Architecture

- Frontend: static Next.js export deployed to Firebase Hosting.
- Backend: FastAPI container deployed to Cloud Run.
- LLM: Gemini 2.5 Flash through Vertex AI on GCP.
- History: current SQLite file stored at `/data/consultation_history.db`, with `/data` mounted from a Cloud Storage bucket.
- SQL artifact: `database/consultation_history.sql` uploaded to Cloud Storage by Terraform.
- Notifications/email: existing SendGrid and Pushover values are injected as runtime secrets.

The Cloud Run service is pinned to max one instance and request concurrency one. That makes the current SQLite approach viable for a small deployment, but Cloud SQL should be the next database step before real clinical or multi-user scale.

## Files Added

- `backend/Dockerfile`: backend-only container for Cloud Run or App Runner.
- `infra/gcp/cloudbuild.backend.yaml`: Cloud Build config for the backend image.
- `frontend/firebase.json` and `frontend/.firebaserc`: Firebase Hosting config.
- `infra/gcp/main.tf`: GCP deploy Terraform.
- `infra/aws/main.tf`: AWS App Runner deploy Terraform.
- `infra/scripts/deploy_gcp.sh` and `infra/scripts/deploy_aws.sh`: provider-specific deployment scripts.
- `infra/scripts/destroy_gcp.sh` and `infra/scripts/destroy_aws.sh`: provider-specific destroy scripts.
- `database/consultation_history.sql`: SQLite schema artifact.

## Infra Commands

Run these from the repo root:

```bash
infra/scripts/deploy_gcp.sh
infra/scripts/destroy_gcp.sh
infra/scripts/deploy_aws.sh
infra/scripts/destroy_aws.sh
```

## GCP Migration Runbook

One-command deployment from the repo root:

```bash
infra/scripts/deploy_gcp.sh
```

The script loads `.env`, deploys the Cloud Run backend through Terraform and Cloud Build, builds the static Next.js frontend with the Cloud Run URL, and deploys Firebase Hosting.

Manual deployment steps are below if you want to run each phase yourself.

Set these values from your existing `.env` without committing secrets:

```bash
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
export TF_VAR_backend_image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/medinotes/medinotes-backend:latest"
```

Bootstrap APIs and Artifact Registry:

```bash
cd infra/gcp
terraform init
terraform apply \
  -target=google_project_service.gcp_apis \
  -target=google_artifact_registry_repository.backend
```

Build and push the backend image:

```bash
cd ../..
gcloud builds submit \
  --config infra/gcp/cloudbuild.backend.yaml \
  --substitutions _IMAGE="$TF_VAR_backend_image" \
  .
```

Deploy the full GCP stack:

```bash
cd infra/gcp
terraform apply
```

Build and deploy Firebase Hosting after Terraform prints `backend_url`:

```bash
cd ../..
export NEXT_PUBLIC_API_BASE_URL="<backend_url_from_terraform>"
export NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="<your_clerk_publishable_key>"
npm --prefix frontend run build
(cd frontend && firebase deploy --only hosting --project "$FIREBASE_PROJECT_ID")
```

Destroy GCP resources:

```bash
infra/scripts/destroy_gcp.sh
```

## AWS Runbook

`infra/aws/main.tf` describes a comparable App Runner deployment using ECR, Secrets Manager, and an S3 SQL artifact bucket. This is useful if you need to recreate or compare the AWS side, but it is separate from the GCP migration state.

AWS needs a Gemini Developer API key because it cannot use Vertex AI service account auth:

```bash
GEMINI_API_KEY="<gemini_api_key>"
```

Deploy AWS:

```bash
infra/scripts/deploy_aws.sh
```

Destroy AWS resources:

```bash
infra/scripts/destroy_aws.sh
```

## Cutover Checklist

1. Deploy Cloud Run and confirm `GET /health` returns `{"status":"healthy"}`.
2. Build Firebase with `NEXT_PUBLIC_API_BASE_URL` set to the Cloud Run URL.
3. Add the Firebase URLs to Clerk allowed origins and redirect URLs.
4. Test sign-in, consultation generation, history reload, SendGrid email, and Pushover notification.
5. Stop or destroy the AWS App Runner service after the Firebase site is live.
