# TEMP(M0): probe Lambda for the Monty experiments (docs/m0-findings.md, E3).
# Delete this file and lambdas/probe/ once the real runner Lambdas exist (M3).
# Build the zip first: `make build LAMBDA=probe`.

resource "aws_iam_role" "probe" {
  name = "openrx-probe"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_lambda_function" "probe" {
  function_name    = "openrx-probe"
  role             = aws_iam_role.probe.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = "${path.module}/build/probe.zip"
  source_code_hash = filebase64sha256("${path.module}/build/probe.zip")

  timeout     = 30
  memory_size = 512

  environment {
    variables = {
      # pip --target puts the monty binary in bin/, which Monty does not search.
      MONTY_BIN = "/var/task/bin/monty"
    }
  }
}
