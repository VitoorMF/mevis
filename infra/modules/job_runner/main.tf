# Instância de processamento sob demanda (doc 03 §7.8, ADR-14). Só o modelo de lançamento, o papel
# e o grupo de logs ficam criados; parados, não custam nada. Cada execução é lançada pelo operador
# com scripts/run-job.sh e se encerra (e se apaga) sozinha.

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "jobs" {
  name              = "/${var.name}/jobs"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "job" {
  name               = "${var.name}-job"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Só para investigar uma execução em andamento (Session Manager); sem SSH.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.job.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  read_prefixes  = ["artifacts", "normalized", "derived/quality_mask"]
  write_prefixes = ["derived/minute_trades", "derived/minute_book", "derived/ops/jobs"]
}

# Privilégio mínimo (ADR-08, RNF-014): lê o código e as entradas, grava só as saídas dos agregados
# e o manifesto. Sem s3:DeleteObject.
data "aws_iam_policy_document" "job" {
  statement {
    sid       = "ReadInputs"
    actions   = ["s3:GetObject"]
    resources = [for p in local.read_prefixes : "${var.bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject"]
    resources = [for p in local.write_prefixes : "${var.bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "ListInputsAndOutputs"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in concat(local.read_prefixes, local.write_prefixes) : "${p}/*"]
    }
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.jobs.arn}:*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "${var.name}-job"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

resource "aws_iam_instance_profile" "job" {
  name = "${var.name}-job"
  role = aws_iam_role.job.name
}

resource "aws_launch_template" "job" {
  name        = "${var.name}-job"
  description = "Processamento sob demanda (doc 03 secao 7.8); lancado por scripts/run-job.sh"

  # AMI resolvida no lançamento pelo parâmetro público do SSM: sempre a mais recente, como o host.
  image_id                             = "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
  instance_type                        = var.instance_type
  instance_initiated_shutdown_behavior = "terminate"
  ebs_optimized                        = true
  update_default_version               = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.job.arn
  }

  network_interfaces {
    subnet_id                   = var.subnet_id
    security_groups             = [var.security_group_id]
    associate_public_ip_address = true
    delete_on_termination       = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = var.root_volume_gb
      encrypted             = true
      delete_on_termination = true
    }
  }

  # O script de cada execução vem do run-job.sh e substitui este. Lançado sem ele (por engano, pelo
  # console), a instância se desliga logo no primeiro boot e é apagada.
  user_data = base64encode("#!/bin/bash\nshutdown -h now\n")

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-job" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${var.name}-job" }
  }
}
