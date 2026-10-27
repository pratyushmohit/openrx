terraform {
  required_version = ">= 1.16"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # "~> 6.70" allows 6.70 and later 6.x releases, but not 7.0, which may
      # contain breaking changes. The exact version is pinned in
      # .terraform.lock.hcl.
      version = "~> 6.70"
    }
    # Builds Lambda zip files from source folders.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}
