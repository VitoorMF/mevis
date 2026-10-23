variable "name" {
  type = string
}

variable "cidr_block" {
  type    = string
  default = "10.42.0.0/24"
}

variable "availability_zone" {
  type        = string
  description = "Zona única (doc 03 §7.2); escolhida entre as que oferecem o tipo de instância."
}
