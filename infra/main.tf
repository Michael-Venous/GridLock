# Gridlock alert service: a daily filing watcher and a small sign-up API, both Lambda functions built from
# one container image (backend/Dockerfile). Everything here fits in the AWS free tier at hackathon scale.
# Deploy with infra/deploy.sh, which builds and pushes the image first.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  profile             = var.aws_profile
  region              = var.region
  allowed_account_ids = [var.account_id] # refuses to touch any other account
  default_tags { tags = { project = var.name } }
}

resource "random_id" "suffix" { byte_length = 4 }

# Keys subscriber ids to email addresses without storing a guessable id.
resource "random_password" "id_secret" {
  length  = 40
  special = false
}

# ---------- storage ----------
resource "aws_s3_bucket" "state" {
  bucket        = "${var.name}-state-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "subscribers" {
  name         = "${var.name}-subscribers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  ttl {
    attribute_name = "expires" # rate-limit counters expire; subscribers have no expiry
    enabled        = true
  }
}

# ---------- email ----------
resource "aws_sns_topic" "alerts" { name = "${var.name}-alerts" }
resource "aws_sns_topic" "maintainers" { name = "${var.name}-maintainers" }

resource "aws_sns_topic_subscription" "maintainer" {
  count     = var.maintainer_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.maintainers.arn
  protocol  = "email"
  endpoint  = var.maintainer_email
}

# ---------- image ----------
resource "aws_ecr_repository" "app" {
  name                 = var.name
  force_delete         = true
  image_tag_mutability = "IMMUTABLE"
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "keep the last three images",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 },
    action       = { type = "expire" }
  }] })
}

# ---------- functions ----------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${var.name}-worker"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 14
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.worker.arn}:*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.state.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.state.arn}/*" },
    { Effect = "Allow", Action = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem"], Resource = aws_dynamodb_table.subscribers.arn },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = [aws_sns_topic.alerts.arn, aws_sns_topic.maintainers.arn] },
    { Effect = "Allow", Action = ["sns:GetSubscriptionAttributes"], Resource = "${aws_sns_topic.alerts.arn}:*" },
  ] })
}

resource "aws_iam_role" "api" {
  name               = "${var.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.api.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.state.arn}/state/changes.json" },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.subscribers.arn },
    { Effect = "Allow", Action = ["sns:Subscribe", "sns:Publish"], Resource = aws_sns_topic.alerts.arn },
    { Effect = "Allow", Action = ["sns:GetSubscriptionAttributes"], Resource = "${aws_sns_topic.alerts.arn}:*" },
  ] })
}

locals {
  env = {
    BUCKET          = aws_s3_bucket.state.id
    TABLE           = aws_dynamodb_table.subscribers.name
    TOPIC_ARN       = aws_sns_topic.alerts.arn
    MAINTAINERS_ARN = aws_sns_topic.maintainers.arn
    ID_SECRET       = random_password.id_secret.result
    APP_URL         = var.app_url
  }
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

resource "aws_lambda_function" "worker" {
  function_name = "${var.name}-worker"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"]
  timeout       = 900
  memory_size   = 2048
  ephemeral_storage { size = 2048 }
  image_config { command = ["worker.handler"] }
  environment { variables = local.env }
  depends_on = [aws_cloudwatch_log_group.worker, aws_iam_role_policy.worker]
}

resource "aws_lambda_function" "api" {
  function_name = "${var.name}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"]
  timeout       = 20
  memory_size   = 512
  image_config { command = ["api.handler"] }
  environment { variables = local.env }
  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE" # the Changes tab calls it from planners' browsers; the handler validates everything
  cors {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

# Function URLs created since October 2025 need both permissions; the second is limited to calls made through the
# URL, so the public can't invoke the function any other way.
resource "aws_lambda_permission" "api_url" {
  statement_id           = "public-function-url"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "api_invoke" {
  statement_id             = "public-invoke-via-url"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}

# ---------- daily schedule ----------
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.worker.arn },
  ] })
}

resource "aws_scheduler_schedule" "watch" {
  name                         = "${var.name}-watch"
  schedule_expression          = var.schedule
  schedule_expression_timezone = "America/New_York"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.worker.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ task = "watch" })
    retry_policy { maximum_retry_attempts = 0 }
  }
}

# ---------- optional spending alarm ----------
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 20
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}
