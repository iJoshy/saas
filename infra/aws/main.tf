terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-1"
}

variable "app_name" {
  description = "Application name prefix."
  type        = string
  default     = "medinotes"
}

variable "backend_image" {
  description = "ECR image URI for App Runner, for example ACCOUNT.dkr.ecr.eu-west-1.amazonaws.com/medinotes-backend:latest."
  type        = string
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

variable "gemini_api_key" {
  description = "Gemini Developer API key for non-GCP deployments."
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

variable "cors_allowed_origins" {
  description = "Comma-separated browser origins allowed to call App Runner."
  type        = string
  default     = "*"
}

locals {
  runtime_secrets = {
    CLERK_SECRET_KEY      = var.clerk_secret_key
    GEMINI_API_KEY        = var.gemini_api_key
    SENDGRID_API_KEY      = var.sendgrid_api_key
    SENDGRID_SENDER_EMAIL = var.sendgrid_sender_email
    PUSHOVER_TOKEN        = var.pushover_token
    PUSHOVER_USER         = var.pushover_user
  }
}

resource "aws_ecr_repository" "backend" {
  name                 = "${var.app_name}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_s3_bucket" "sql_artifacts" {
  bucket_prefix = "${var.app_name}-sql-"
}

resource "aws_s3_object" "schema" {
  bucket = aws_s3_bucket.sql_artifacts.id
  key    = "schema/consultation_history.sql"
  source = "${path.module}/../../database/consultation_history.sql"
  etag   = filemd5("${path.module}/../../database/consultation_history.sql")
}

resource "aws_secretsmanager_secret" "runtime" {
  for_each = local.runtime_secrets
  name     = "${var.app_name}/${each.key}"
}

resource "aws_secretsmanager_secret_version" "runtime" {
  for_each      = local.runtime_secrets
  secret_id     = aws_secretsmanager_secret.runtime[each.key].id
  secret_string = each.value
}

data "aws_iam_policy_document" "apprunner_ecr_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name               = "${var.app_name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_ecr_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr_access" {
  role       = aws_iam_role.apprunner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

data "aws_iam_policy_document" "apprunner_instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${var.app_name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_instance_assume.json
}

data "aws_iam_policy_document" "runtime_secrets" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [for secret in aws_secretsmanager_secret.runtime : secret.arn]
  }
}

resource "aws_iam_role_policy" "runtime_secrets" {
  name   = "${var.app_name}-runtime-secrets"
  role   = aws_iam_role.apprunner_instance.id
  policy = data.aws_iam_policy_document.runtime_secrets.json
}

resource "aws_apprunner_service" "backend" {
  service_name = "${var.app_name}-backend"

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    image_repository {
      image_identifier      = var.backend_image
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"

        runtime_environment_variables = {
          CLERK_JWKS_URL            = var.clerk_jwks_url
          CORS_ALLOWED_ORIGINS      = var.cors_allowed_origins
          GEMINI_MODEL              = "gemini-2.5-flash"
          GOOGLE_GENAI_USE_VERTEXAI = "false"
          HISTORY_DB_PATH           = "/tmp/consultation_history.db"
        }

        runtime_environment_secrets = {
          for key, secret in aws_secretsmanager_secret.runtime : key => secret.arn
        }
      }
    }
  }

  instance_configuration {
    cpu               = "0.25 vCPU"
    memory            = "0.5 GB"
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  depends_on = [
    aws_iam_role_policy.runtime_secrets,
    aws_iam_role_policy_attachment.apprunner_ecr_access,
    aws_secretsmanager_secret_version.runtime,
  ]
}

output "backend_url" {
  value = "https://${aws_apprunner_service.backend.service_url}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "sql_artifact_bucket" {
  value = aws_s3_bucket.sql_artifacts.id
}
