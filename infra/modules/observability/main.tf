# Logs, alertas por e-mail (ADR-12) e orçamento (doc 03 §7.5 e §10.5). Os alarmes que dependem
# da instância ficam no módulo collector_host.

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/${var.name}/collector"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/${var.name}/pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

# A AWS envia um e-mail de confirmação; os alertas só chegam depois de aceito.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Orçamento sobre o custo bruto (sem descontar créditos), para acompanhar o consumo dos créditos.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-mensal"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
