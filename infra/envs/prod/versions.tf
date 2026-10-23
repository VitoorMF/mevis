terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Estado no bucket criado por infra/bootstrap, com bloqueio nativo do S3 (ADR-10).
  backend "s3" {
    bucket       = "trader-tfstate-019419901344-us-east-2"
    key          = "prod/terraform.tfstate"
    region       = "us-east-2"
    profile      = "trader"
    encrypt      = true
    use_lockfile = true
  }
}
