output "bucket_name" {
  value = aws_s3_bucket.this.id
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}

output "url" {
  description = "Endereço público da página de status."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}
