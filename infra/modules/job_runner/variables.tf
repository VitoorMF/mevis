variable "name" {
  type = string
}

variable "instance_type" {
  type        = string
  default     = "c7i-flex.large"
  description = "2 vCPUs x86 e 4 GiB, elegível ao plano gratuito da conta (DEC-33, doc 03 §7.8)."
}

variable "root_volume_gb" {
  type    = number
  default = 20
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type        = string
  description = "O do host: sem entrada, saída só HTTPS (ADR-06)."
}

variable "bucket_arn" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}
