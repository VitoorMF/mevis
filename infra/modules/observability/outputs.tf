output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "log_group_arns" {
  value = [aws_cloudwatch_log_group.collector.arn, aws_cloudwatch_log_group.pipeline.arn]
}

output "collector_log_group" {
  value = aws_cloudwatch_log_group.collector.name
}

output "pipeline_log_group" {
  value = aws_cloudwatch_log_group.pipeline.name
}
