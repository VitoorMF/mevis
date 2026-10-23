variable "bucket_name" {
  type        = string
  description = "Nome global do bucket."
}

variable "noncurrent_version_days" {
  type        = number
  default     = 30
  description = "Dias até apagar versões substituídas (doc 03 §7.4)."
}
