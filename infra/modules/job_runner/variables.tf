variable "name" {
  type = string
}

variable "instance_type" {
  type        = string
  default     = "c7g.2xlarge"
  description = "8 vCPUs Graviton e 16 GiB (PD-23); verificado em 06/11/2026 (doc 03 §7.8)."
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
