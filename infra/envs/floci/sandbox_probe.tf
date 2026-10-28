# TEMP(M3): probe Lambda for the sandbox wrapper (M3 piece 3). Delete this file
# and lambdas/sandbox_probe/ when the runner Lambdas exist (pieces 4-5).
# Build first: `make build LAMBDA=sandbox_probe`.

resource "aws_iam_role" "sandbox_probe" {
  name = "openrx-sandbox-probe"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_lambda_function" "sandbox_probe" {
  function_name    = "openrx-sandbox-probe"
  role             = aws_iam_role.sandbox_probe.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = "${path.module}/build/sandbox_probe.zip"
  source_code_hash = filebase64sha256("${path.module}/build/sandbox_probe.zip")
  memory_size      = 512
  timeout          = 90

  environment {
    variables = {
      # pip --target puts the monty binary in bin/, which Monty does not search.
      MONTY_BIN = "/var/task/bin/monty"
    }
  }
}
