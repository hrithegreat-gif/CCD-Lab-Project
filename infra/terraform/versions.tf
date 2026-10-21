terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Exact pin; .terraform.lock.hcl records the verified checksums.
      version = "5.100.0"
    }
  }
}

# Primary provider: every resource in this lab lives in ap-south-1 (Mumbai).
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_prefix
      ManagedBy   = "terraform"
      Environment = "aws-demo"
    }
  }
}

# AWS Budgets is a global billing service: its API only exists in us-east-1.
provider "aws" {
  alias  = "global"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = var.project_prefix
      ManagedBy   = "terraform"
      Environment = "aws-demo"
    }
  }
}
