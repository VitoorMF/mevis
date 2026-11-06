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

output "job_launch_template" {
  description = "Modelo de lançamento da instância sob demanda (doc 03 §7.8)."
  value       = module.job_runner.launch_template_name
}

output "job_log_group" {
  value = module.job_runner.log_group_name
}

output "uv_version" {
  description = "Versão do uv instalada no host e na instância sob demanda."
  value       = var.uv_version
}
