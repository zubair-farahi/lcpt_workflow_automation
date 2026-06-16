terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Recommended once a shared AWS account is confirmed: keep state in S3
  # with locking, instead of on one laptop.
  #
  # backend "s3" {
  #   bucket       = "<your-terraform-state-bucket>"
  #   key          = "lcpt-scan-automation/terraform.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  # When aws_profile is null, Terraform falls back to the standard
  # credential chain (AWS_PROFILE env var, default profile, IMDS, etc.).
  # When set, this profile MUST have rights to create ECR/Lambda/IAM/
  # CloudWatch/Secrets Manager resources — see variables.tf for the
  # full warning about scoped profiles like "fw-ocr-s3".
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "lcpt-scan-automation"
      ManagedBy = "terraform"
    }
  }
}
