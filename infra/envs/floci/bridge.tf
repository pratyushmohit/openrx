# The bridge Lambda (README §8.6): the only component that holds API keys and
# calls the public APIs. Build its zip first: `make build LAMBDA=bridge`
# (or `make deploy`, which builds and applies).

# --- fixtures bucket (D-013) ---

resource "aws_s3_bucket" "fixtures" {
  bucket = "openrx-fixtures"
  # make up wipes Floci anyway (D-003); this lets destroy remove a non-empty bucket.
  force_destroy = true
}

# --- IAM: what the bridge may do, and nothing more ---

resource "aws_iam_role" "bridge" {
  name = "openrx-bridge"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bridge" {
  name = "bridge"
  role = aws_iam_role.bridge.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadApiKeys"
        Effect = "Allow"
        Action = "secretsmanager:GetSecretValue"
        Resource = [
          aws_secretsmanager_secret.openfda_api_key.arn,
          aws_secretsmanager_secret.ncbi_api_key.arn,
        ]
      },
      {
        Sid      = "Fixtures"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.fixtures.arn}/fixtures/*"
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/openrx-bridge*"
      },
    ]
  })
}

# --- the function ---

locals {
  bridge_zip = "${path.module}/build/bridge.zip"
}

resource "aws_lambda_function" "bridge" {
  function_name    = "openrx-bridge"
  role             = aws_iam_role.bridge.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = local.bridge_zip
  source_code_hash = filebase64sha256(local.bridge_zip)

  memory_size = 256
  # Room for upstream retries (3 attempts, 20 s read timeout) plus rate-limit waits.
  timeout = 90

  # One container at a time, so in-memory rate limits and the call budget see
  # every request (D-012).
  reserved_concurrent_executions = 1

  environment {
    variables = {
      OPENRX_FIXTURE_MODE   = var.fixture_mode
      OPENRX_FIXTURE_BUCKET = aws_s3_bucket.fixtures.bucket
      OPENRX_CONTACT_EMAIL  = var.contact_email
      # Secrets are read by ARN (D-007); the catalog names them by logical name.
      OPENRX_SECRET_ARNS = jsonencode({
        (aws_secretsmanager_secret.openfda_api_key.name) = aws_secretsmanager_secret.openfda_api_key.arn
        (aws_secretsmanager_secret.ncbi_api_key.name)    = aws_secretsmanager_secret.ncbi_api_key.arn
      })
    }
  }
}
