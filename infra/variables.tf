variable "aws_profile" {
  description = "AWS CLI profile for the account to deploy into."
  type        = string
}

variable "account_id" {
  description = "The 12-digit account ID the profile must resolve to. Terraform refuses any other account."
  type        = string
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "gridlock"
}

variable "image_tag" {
  description = "Tag of the image in ECR (deploy.sh passes a new one each time, so the functions pick up the new image)."
  type        = string
  default     = "unset"
}

variable "allowed_origins" {
  description = "Web origins allowed to call the sign-up API (the local dev server, plus the hosted site if there is one)."
  type        = list(string)
  default     = ["http://127.0.0.1:8000", "http://localhost:8000"]
}

variable "app_url" {
  description = "Public address of the app, linked from alert emails. Empty leaves the link out."
  type        = string
  default     = ""
}

variable "schedule" {
  description = "When the watcher runs (EventBridge Scheduler expression, New York time)."
  type        = string
  default     = "cron(0 7 * * ? *)"
}

variable "maintainer_email" {
  description = "Who hears about filings the pipeline can't read. Empty skips the subscription."
  type        = string
  default     = ""
}

variable "budget_email" {
  description = "If set, a $5/month budget emails this address once spending passes $1."
  type        = string
  default     = ""
}
