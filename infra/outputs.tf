output "api_url" {
  description = "Put this in data/alerts.json as apiUrl (deploy.sh does)."
  value       = aws_lambda_function_url.api.function_url
}

output "bucket" { value = aws_s3_bucket.state.id }
output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "alerts_topic_arn" { value = aws_sns_topic.alerts.arn }
output "worker_function" { value = aws_lambda_function.worker.function_name }
