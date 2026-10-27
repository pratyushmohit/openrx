output "openfda_api_key_secret_arn" {
  description = "ARN of the openFDA API key secret."
  value       = aws_secretsmanager_secret.openfda_api_key.arn
}
