variable "account_id" {
  type        = string
  description = "Conta nova, com os créditos (ADR-09). O provider recusa qualquer outra."
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "aws_profile" {
  type    = string
  default = "trader"
}

variable "alert_email" {
  type      = string
  sensitive = true
}

variable "instance_type" {
  type    = string
  default = "t4g.small"
}

variable "root_volume_gb" {
  type    = number
  default = 16
}

variable "monthly_budget_usd" {
  type    = number
  default = 20
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "uv_version" {
  type    = string
  default = "0.12.15"
}

variable "app_alarms_enabled" {
  type    = bool
  default = false
}
