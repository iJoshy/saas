terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.27"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.27"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "GCP project that will host Cloud Run, Artifact Registry, Vertex AI, secrets, and storage."
  type        = string
}

variable "region" {
  description = "Primary deployment region."
  type        = string
  default     = "europe-west1"
}

variable "vertex_location" {
  description = "Vertex AI Gemini location. europe-west1 is available for gemini-2.5-flash."
  type        = string
  default     = "europe-west1"
}

variable "backend_image" {
  description = "Container image for the backend, for example europe-west1-docker.pkg.dev/PROJECT/medinotes/medinotes-backend:SHA."
  type        = string
}

variable "app_version" {
  description = "Application version label exposed by the backend health/version endpoints."
  type        = string
  default     = "local"
}

variable "firebase_site_id" {
  description = "Firebase Hosting site ID."
  type        = string
  default     = "medinotes-studio"
}

variable "firebase_project_id" {
  description = "Firebase project ID. Leave null to use project_id."
  type        = string
  default     = null
}

variable "history_bucket_name" {
  description = "Globally unique Cloud Storage bucket for SQLite history and SQL bootstrap artifacts. Leave null to derive from project_id."
  type        = string
  default     = null
}

variable "clerk_jwks_url" {
  description = "Clerk JWKS URL used by the FastAPI auth guard."
  type        = string
}

variable "clerk_secret_key" {
  description = "Clerk secret key, kept available for future backend Clerk calls."
  type        = string
  sensitive   = true
}

variable "sendgrid_api_key" {
  description = "SendGrid API key."
  type        = string
  sensitive   = true
}

variable "sendgrid_sender_email" {
  description = "Verified SendGrid sender email."
  type        = string
  sensitive   = true
}

variable "pushover_token" {
  description = "Pushover application token."
  type        = string
  sensitive   = true
}

variable "pushover_user" {
  description = "Pushover user key."
  type        = string
  sensitive   = true
}

variable "extra_cors_origins" {
  description = "Additional browser origins allowed to call Cloud Run."
  type        = list(string)
  default     = []
}

locals {
  app_name            = "medinotes"
  firebase_project_id = coalesce(var.firebase_project_id, var.project_id)
  history_bucket_name = coalesce(var.history_bucket_name, "${var.project_id}-medinotes-history")
  firebase_origins = [
    "https://${var.firebase_site_id}.web.app",
    "https://${var.firebase_site_id}.firebaseapp.com",
  ]
  cors_allowed_origins = join(",", concat(local.firebase_origins, var.extra_cors_origins))
  runtime_secrets = {
    CLERK_SECRET_KEY      = var.clerk_secret_key
    SENDGRID_API_KEY      = var.sendgrid_api_key
    SENDGRID_SENDER_EMAIL = var.sendgrid_sender_email
    PUSHOVER_TOKEN        = var.pushover_token
    PUSHOVER_USER         = var.pushover_user
  }
}

resource "google_project_service" "gcp_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_project_service" "firebase_apis" {
  for_each = toset([
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
  ])

  project            = local.firebase_project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "backend" {
  repository_id = local.app_name
  location      = var.region
  format        = "DOCKER"
  description   = "MediNotes backend containers"

  depends_on = [google_project_service.gcp_apis]
}

resource "google_storage_bucket" "history" {
  name                        = local.history_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.gcp_apis]
}

resource "google_storage_bucket_object" "schema" {
  name   = "schema/consultation_history.sql"
  bucket = google_storage_bucket.history.name
  source = "${path.module}/../../database/consultation_history.sql"
}

resource "google_secret_manager_secret" "runtime" {
  for_each  = local.runtime_secrets
  secret_id = lower(replace(each.key, "_", "-"))

  replication {
    auto {}
  }

  depends_on = [google_project_service.gcp_apis]
}

resource "google_secret_manager_secret_version" "runtime" {
  for_each    = local.runtime_secrets
  secret      = google_secret_manager_secret.runtime[each.key].id
  secret_data = each.value
}

resource "google_service_account" "cloud_run" {
  account_id   = "medinotes-cloud-run"
  display_name = "MediNotes Cloud Run runtime"
}

resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_storage_bucket_iam_member" "history_object_admin" {
  bucket = google_storage_bucket.history.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_cloud_run_v2_service" "backend" {
  name     = "medinotes-backend"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.cloud_run.email
    max_instance_request_concurrency = 1

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    volumes {
      name = "history"
      gcs {
        bucket    = google_storage_bucket.history.name
        read_only = false
      }
    }

    containers {
      image = var.backend_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      volume_mounts {
        name       = "history"
        mount_path = "/data"
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.vertex_location
      }

      env {
        name  = "GEMINI_MODEL"
        value = "gemini-2.5-flash"
      }

      env {
        name  = "APP_VERSION"
        value = var.app_version
      }

      env {
        name  = "CLERK_JWKS_URL"
        value = var.clerk_jwks_url
      }

      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = local.cors_allowed_origins
      }

      env {
        name  = "HISTORY_DB_PATH"
        value = "/data/consultation_history.db"
      }

      dynamic "env" {
        for_each = google_secret_manager_secret.runtime
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.secret_accessor,
    google_project_iam_member.vertex_user,
    google_secret_manager_secret_version.runtime,
    google_storage_bucket_iam_member.history_object_admin,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.backend.location
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_firebase_project" "default" {
  provider = google-beta
  project  = local.firebase_project_id

  depends_on = [google_project_service.firebase_apis]
}

resource "google_firebase_web_app" "default" {
  provider        = google-beta
  project         = google_firebase_project.default.project
  display_name    = "MediNotes Web"
  deletion_policy = "DELETE"
}

resource "google_firebase_hosting_site" "default" {
  provider = google-beta
  project  = google_firebase_project.default.project
  site_id  = var.firebase_site_id
  app_id   = google_firebase_web_app.default.app_id
}

output "backend_url" {
  value = google_cloud_run_v2_service.backend.uri
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.backend.name
}

output "suggested_backend_image" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}/medinotes-backend:<version>"
}

output "firebase_hosting_urls" {
  value = local.firebase_origins
}

output "frontend_build_env" {
  value = "NEXT_PUBLIC_API_BASE_URL=${google_cloud_run_v2_service.backend.uri}"
}
