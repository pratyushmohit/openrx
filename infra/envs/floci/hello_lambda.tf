# TEMP(M0): hello-world Lambda, to prove that Terraform can deploy a python3.13
# Lambda to Floci and that Floci can run it in Docker. Delete this file (and
# lambdas/hello/) once the real runner Lambdas exist (M3).

# Zip the handler source. Terraform rebuilds the zip whenever the code changes.
data "archive_file" "hello" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/hello"
  output_path = "${path.module}/build/hello.zip"
}

# Every Lambda runs as an IAM role. The "assume role policy" says who may use
# the role: here, only the Lambda service. The role grants no permissions.
resource "aws_iam_role" "hello" {
  name = "openrx-hello"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_lambda_function" "hello" {
  function_name = "openrx-hello"
  role          = aws_iam_role.hello.arn
  runtime       = "python3.13"
  handler       = "handler.handler" # file handler.py, function handler()

  filename = data.archive_file.hello.output_path
  # Hash of the zip: a code change produces a new hash, which tells Terraform
  # to upload the new code.
  source_code_hash = data.archive_file.hello.output_base64sha256

  timeout     = 10
  memory_size = 128
}
