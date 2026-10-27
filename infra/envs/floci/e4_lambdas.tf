# TEMP(M0): E4 experiment, a runner Lambda invoking a bridge Lambda that reads
# Secrets Manager (docs/m0-findings.md, E4). Delete this file and
# lambdas/e4_runner, lambdas/e4_bridge once the real Lambdas exist (M3).
# Build first: `make build LAMBDA=e4_runner` and `make build LAMBDA=e4_bridge`.

locals {
  lambda_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# --- bridge: may read the openFDA secret, nothing else ---

resource "aws_iam_role" "e4_bridge" {
  name               = "openrx-e4-bridge"
  assume_role_policy = local.lambda_assume_role_policy
}

resource "aws_iam_role_policy" "e4_bridge_read_secret" {
  name = "read-openfda-secret"
  role = aws_iam_role.e4_bridge.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = aws_secretsmanager_secret.openfda_api_key.arn
    }]
  })
}

resource "aws_lambda_function" "e4_bridge" {
  function_name    = "openrx-e4-bridge"
  role             = aws_iam_role.e4_bridge.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = "${path.module}/build/e4_bridge.zip"
  source_code_hash = filebase64sha256("${path.module}/build/e4_bridge.zip")
  timeout          = 10
  memory_size      = 256

  environment {
    variables = { OPENFDA_SECRET_ID = aws_secretsmanager_secret.openfda_api_key.name }
  }
}

# --- runner: may invoke the bridge, nothing else ---

resource "aws_iam_role" "e4_runner" {
  name               = "openrx-e4-runner"
  assume_role_policy = local.lambda_assume_role_policy
}

resource "aws_iam_role_policy" "e4_runner_invoke_bridge" {
  name = "invoke-bridge"
  role = aws_iam_role.e4_runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.e4_bridge.arn
    }]
  })
}

resource "aws_lambda_function" "e4_runner" {
  function_name    = "openrx-e4-runner"
  role             = aws_iam_role.e4_runner.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = "${path.module}/build/e4_runner.zip"
  source_code_hash = filebase64sha256("${path.module}/build/e4_runner.zip")
  timeout          = 30
  memory_size      = 256

  environment {
    variables = { BRIDGE_FUNCTION = aws_lambda_function.e4_bridge.function_name }
  }
}
