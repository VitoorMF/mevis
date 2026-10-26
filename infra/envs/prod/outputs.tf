output "instance_id" {
  value = module.collector_host.instance_id
}

output "public_ip" {
  value = module.collector_host.public_ip
}

output "data_bucket" {
  value = module.data_bucket.name
}

output "sns_topic_arn" {
  value = module.observability.sns_topic_arn
}

output "ssm_session" {
  description = "Abre um terminal no host, sem SSH."
  value       = "aws ssm start-session --profile ${var.aws_profile} --region ${var.region} --target ${module.collector_host.instance_id}"
}

output "public_status_bucket" {
  value = module.public_status.bucket_name
}

output "public_status_url" {
  description = "Página pública de status (ADR-13)."
  value       = module.public_status.url
}
