variable "name" {
  type = string
}

variable "instance_type" {
  type        = string
  description = "t4g.small até o CP-1 (doc 03 §10.4); t4g.micro é a alternativa no plano gratuito."
}

variable "root_volume_gb" {
  type    = number
  default = 16
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "log_group_arns" {
  type = list(string)
}

variable "collector_log_group" {
  type = string
}

variable "pipeline_log_group" {
  type = string
}

variable "sns_topic_arn" {
  type = string
}

variable "metrics_namespace" {
  type    = string
  default = "Trader"
}

variable "uv_version" {
  type        = string
  description = "Versão do uv instalada no host (a mesma do desenvolvimento)."
}

variable "app_alarms_enabled" {
  type        = bool
  description = "Liga as ações dos alarmes da aplicação; deixe falso até o coletor rodar."
}
