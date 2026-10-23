# Host de coleta (doc 03 §7.3): EC2 Graviton com Amazon Linux 2023, papel IAM de privilégio
# mínimo (sem apagar no S3 — ADR-08), créditos de CPU `standard` (ADR-07) e alarmes.

data "aws_region" "current" {}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# ------------------------------------------------------------------------------ IAM

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "collector" {
  name               = "${var.name}-collector"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.collector.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  write_prefixes = ["raw", "normalized", "derived", "quarantine", "ops"]
  read_prefixes  = concat(local.write_prefixes, ["artifacts"])
}

data "aws_iam_policy_document" "collector" {
  statement {
    sid       = "WriteData"
    actions   = ["s3:PutObject"]
    resources = [for p in local.write_prefixes : "${var.bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "ReadData"
    actions   = ["s3:GetObject"]
    resources = [for p in local.read_prefixes : "${var.bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [for arn in var.log_group_arns : "${arn}:*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "PutMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.metrics_namespace]
    }
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "${var.name}-collector"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}

resource "aws_iam_instance_profile" "collector" {
  name = "${var.name}-collector"
  role = aws_iam_role.collector.name
}

# ------------------------------------------------------------------------------ instância

resource "aws_instance" "collector" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.collector.name
  associate_public_ip_address = true
  disable_api_termination     = true
  monitoring                  = false

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "${var.name}-collector" }
  }

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    bucket              = var.bucket_name
    region              = data.aws_region.current.region
    uv_version          = var.uv_version
    namespace           = var.metrics_namespace
    collector_log_group = var.collector_log_group
    pipeline_log_group  = var.pipeline_log_group
  })

  tags = { Name = "${var.name}-collector" }

  lifecycle {
    # Nova AMI ou script de inicialização não recriam a instância: atualizações são feitas
    # com trader-deploy pelo SSM (doc 03 §9).
    ignore_changes = [ami, user_data]
  }
}

# ------------------------------------------------------------------------------ alarmes

locals {
  # O agente do CloudWatch publica métricas StatsD com a dimensão metric_type e, por causa de
  # append_dimensions, com InstanceId (conferido na primeira implantação, 23/10/2026).
  statsd_dimensions = {
    InstanceId  = aws_instance.collector.id
    metric_type = "gauge"
  }
  alarm_actions = [var.sns_topic_arn]
  app_alarms = {
    heartbeat = {
      metric      = "Heartbeat", statistic = "SampleCount", period = 60, periods = 3
      operator    = "LessThanThreshold", threshold = 1, missing = "breaching"
      description = "Coletor sem sinal de vida (RF-164)"
    }
    channel-age = {
      metric      = "MaxChannelAgeSeconds", statistic = "Maximum", period = 60, periods = 3
      operator    = "GreaterThanThreshold", threshold = 30, missing = "notBreaching"
      description = "Algum canal sem mensagens ha mais de 30 s"
    }
    gaps = {
      metric      = "GapsDetected", statistic = "Sum", period = 3600, periods = 1
      operator    = "GreaterThanThreshold", threshold = 5, missing = "notBreaching"
      description = "Mais de 5 lacunas na ultima hora"
    }
    clock-offset = {
      metric      = "ClockOffsetMs", statistic = "Maximum", period = 300, periods = 2
      operator    = "GreaterThanThreshold", threshold = 250, missing = "notBreaching"
      description = "Offset de relogio acima de 250 ms"
    }
    replication-lag = {
      metric      = "ReplicationLagMinutes", statistic = "Maximum", period = 3600, periods = 2
      operator    = "GreaterThanThreshold", threshold = 90, missing = "breaching"
      description = "Replicacao atrasada ou pipeline parado"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "app" {
  for_each = local.app_alarms

  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = var.metrics_namespace
  metric_name         = each.value.metric
  dimensions          = local.statsd_dimensions
  statistic           = each.value.statistic
  period              = each.value.period
  evaluation_periods  = each.value.periods
  comparison_operator = each.value.operator
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  actions_enabled     = var.app_alarms_enabled
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "disk" {
  alarm_name          = "${var.name}-disk"
  alarm_description   = "Disco do host acima de 80%"
  namespace           = var.metrics_namespace
  metric_name         = "disk_used_percent"
  dimensions          = { InstanceId = aws_instance.collector.id, path = "/", fstype = "xfs" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  actions_enabled     = var.app_alarms_enabled
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "system_check" {
  alarm_name          = "${var.name}-system-check"
  alarm_description   = "Falha de hardware: recuperacao automatica do EC2"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.collector.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  alarm_actions       = ["arn:aws:automate:${data.aws_region.current.region}:ec2:recover", var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "instance_check" {
  alarm_name          = "${var.name}-instance-check"
  alarm_description   = "Host travado: reinicializacao automatica"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.collector.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  alarm_actions       = ["arn:aws:automate:${data.aws_region.current.region}:ec2:reboot", var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "cpu_credits" {
  alarm_name          = "${var.name}-cpu-credits"
  alarm_description   = "Saldo de creditos de CPU baixo (modo standard limita o desempenho)"
  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  dimensions          = { InstanceId = aws_instance.collector.id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 30
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}
