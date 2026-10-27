# The standard AWS provider, pointed at Floci instead of real AWS.
# Only this file differs between envs/floci and a future envs/aws.
provider "aws" {
  region = "us-east-1"

  # Floci accepts any credentials. These are not secrets.
  access_key = "test"
  secret_key = "test"

  # By default the provider checks the credentials and looks up the account
  # ID against real AWS. Skip those calls; there is no real AWS here.
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  # Every AWS service Terraform uses must be routed to Floci here. A service
  # missing from this list would silently go to real AWS (and fail on the
  # dummy credentials). Add services as we start using them.
  endpoints {
    iam            = var.floci_endpoint
    lambda         = var.floci_endpoint
    secretsmanager = var.floci_endpoint
    sts            = var.floci_endpoint
  }

  default_tags {
    tags = {
      project = "openrx"
      env     = "floci"
    }
  }
}
