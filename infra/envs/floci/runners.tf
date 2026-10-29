# The runner Lambdas (docs/design.md §8.5): where model-written code runs, in Monty
# (D-005). Build their zips first: `make deploy` builds and applies.

locals {
  lambda_assume_role = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  search_runner_zip = "${path.module}/build/search_runner.zip"
}

# --- search-runner: catalog_* only, no AWS access ---

resource "aws_iam_role" "search_runner" {
  name               = "openrx-search-runner"
  assume_role_policy = local.lambda_assume_role
}

# Its only permission: writing its own logs. No data access of any kind
# (docs/design.md §8.5: "no AWS permissions"); without this, real AWS drops its logs.
resource "aws_iam_role_policy" "search_runner_logs" {
  name = "own-logs"
  role = aws_iam_role.search_runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/openrx-search-runner*"
    }]
  })
}

resource "aws_lambda_function" "search_runner" {
  function_name    = "openrx-search-runner"
  role             = aws_iam_role.search_runner.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = local.search_runner_zip
  source_code_hash = filebase64sha256(local.search_runner_zip)

  memory_size = 512
  # Above the 60 s sandbox limit, so the sandbox times out first with a clear error.
  timeout = 90

  environment {
    variables = {
      # pip --target puts the monty binary in bin/, which Monty does not search.
      MONTY_BIN = "/var/task/bin/monty"
    }
  }
}

# --- execute-runner: catalog_* plus request(), which may only invoke the bridge ---

locals {
  execute_runner_zip = "${path.module}/build/execute_runner.zip"
}

resource "aws_iam_role" "execute_runner" {
  name               = "openrx-execute-runner"
  assume_role_policy = local.lambda_assume_role
}

resource "aws_iam_role_policy" "execute_runner" {
  name = "invoke-bridge"
  role = aws_iam_role.execute_runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Its one real permission (docs/design.md §8.5): request() goes through the bridge.
        Sid      = "InvokeBridgeOnly"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.bridge.arn
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/openrx-execute-runner*"
      },
    ]
  })
}

resource "aws_lambda_function" "execute_runner" {
  function_name    = "openrx-execute-runner"
  role             = aws_iam_role.execute_runner.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = local.execute_runner_zip
  source_code_hash = filebase64sha256(local.execute_runner_zip)

  memory_size = 512
  # Above the 60 s sandbox limit, so the sandbox times out first with a clear error.
  timeout = 90

  environment {
    variables = {
      MONTY_BIN                 = "/var/task/bin/monty"
      OPENRX_BRIDGE_FUNCTION    = aws_lambda_function.bridge.function_name
      OPENRX_BRIDGE_CONCURRENCY = tostring(local.bridge_concurrency)
    }
  }
}
