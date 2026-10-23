variable "name" {
  type = string
}

variable "alert_email" {
  type      = string
  sensitive = true
}

variable "monthly_budget_usd" {
  type        = number
  description = "Teto mensal de referência; R$ 100 ≈ US$ 20 (DEC-16)."
}

variable "log_retention_days" {
  type    = number
  default = 14
}
