terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # No backend configured here on purpose - local state is fine to get
  # started, but real usage needs a remote backend (S3 + a DynamoDB lock
  # table, or Terraform Cloud) with restricted access, since this state file
  # contains the DB password and JWT secret in plaintext. See README.md.
}

provider "aws" {
  region = var.aws_region
}
