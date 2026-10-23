output "instance_id" {
  value = aws_instance.collector.id
}

output "public_ip" {
  value = aws_instance.collector.public_ip
}

output "role_name" {
  value = aws_iam_role.collector.name
}
