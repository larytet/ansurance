terraform {
  required_version = "= 1.9.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # One state per environment. Create the bucket and lock table once (see README), then:
  #   terraform init -backend-config=backend-dev.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "ansurance"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }

  # Offline planning for tests and CI: set offline = true (fake credentials, no API calls).
  access_key                  = var.offline ? "offline" : null
  secret_key                  = var.offline ? "offline" : null
  skip_credentials_validation = var.offline
  skip_requesting_account_id  = var.offline
  skip_metadata_api_check     = var.offline
}
