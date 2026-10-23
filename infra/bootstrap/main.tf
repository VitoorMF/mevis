# Cria o bucket de estado do Terraform (ADR-10). Estado local: rode uma vez, antes de envs/prod.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "account_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "aws_profile" {
  type    = string
  default = "trader"
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project   = "trader"
      Env       = "prod"
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}

module "state_bucket" {
  source      = "../modules/data_bucket"
  bucket_name = "trader-tfstate-${var.account_id}-${var.region}"
}

output "state_bucket" {
  value = module.state_bucket.name
}
