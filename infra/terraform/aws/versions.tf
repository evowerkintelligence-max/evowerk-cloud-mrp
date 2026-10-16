terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # No backend configured here on purpose - local state is fine to get
  # started, but real usage needs encrypted remote state, since this state
  # file contains the DB password and JWT secret in plaintext. See
  # backend.tf.example and README.md.
}

provider "aws" {
  region = var.aws_region
}
