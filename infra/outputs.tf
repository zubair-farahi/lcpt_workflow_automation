output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.app.function_name
}

output "secrets_arn" {
  description = "Set the secret VALUE here via CLI — never in code"
  value       = aws_secretsmanager_secret.runtime.arn
}

output "sqs_queue_url" {
  value = var.enable_s3_trigger ? aws_sqs_queue.scans[0].url : null
}

output "log_group" {
  value = aws_cloudwatch_log_group.lambda.name
}
