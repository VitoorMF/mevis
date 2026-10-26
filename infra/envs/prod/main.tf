# Ambiente de produção em Ohio, us-east-2 (DEC-26). Só roda na conta nova (ADR-09).

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project   = "trader"
      Env       = "prod"
      ManagedBy = "terraform"
    }
  }
}

locals {
  name = "trader"
}

# Primeira zona (em ordem alfabética) que oferece o tipo de instância.
data "aws_ec2_instance_type_offerings" "available" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

module "network" {
  source            = "../../modules/network"
  name              = local.name
  availability_zone = sort(data.aws_ec2_instance_type_offerings.available.locations)[0]
}

module "data_bucket" {
  source      = "../../modules/data_bucket"
  bucket_name = "trader-data-${var.account_id}-${var.region}"
}

module "public_status" {
  source      = "../../modules/public_status"
  name        = local.name
  bucket_name = "trader-public-${var.account_id}-${var.region}"
}

module "observability" {
  source             = "../../modules/observability"
  name               = local.name
  alert_email        = var.alert_email
  monthly_budget_usd = var.monthly_budget_usd
  log_retention_days = var.log_retention_days
}

module "collector_host" {
  source              = "../../modules/collector_host"
  name                = local.name
  instance_type       = var.instance_type
  root_volume_gb      = var.root_volume_gb
  subnet_id           = module.network.subnet_id
  security_group_id   = module.network.security_group_id
  bucket_name         = module.data_bucket.name
  bucket_arn          = module.data_bucket.arn
  log_group_arns      = module.observability.log_group_arns
  collector_log_group = module.observability.collector_log_group
  pipeline_log_group  = module.observability.pipeline_log_group
  sns_topic_arn       = module.observability.sns_topic_arn
  uv_version          = var.uv_version
  app_alarms_enabled  = var.app_alarms_enabled
  public_bucket_arn   = module.public_status.bucket_arn
  public_bucket_name  = module.public_status.bucket_name
}
