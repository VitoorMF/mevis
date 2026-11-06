output "launch_template_name" {
  value = aws_launch_template.job.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.jobs.name
}

output "role_name" {
  value = aws_iam_role.job.name
}
