output "openfda_api_key_secret_arn" {
  description = "ARN of the openFDA API key secret."
  value       = aws_secretsmanager_secret.openfda_api_key.arn
}

output "ncbi_api_key_secret_arn" {
  description = "ARN of the NCBI E-utilities API key secret."
  value       = aws_secretsmanager_secret.ncbi_api_key.arn
}

output "bridge_function_name" {
  description = "Name of the bridge Lambda."
  value       = aws_lambda_function.bridge.function_name
}

output "fixtures_bucket" {
  description = "S3 bucket holding replay fixtures."
  value       = aws_s3_bucket.fixtures.bucket
}
