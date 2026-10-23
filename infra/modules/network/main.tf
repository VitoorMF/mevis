# Rede mínima (ADR-06): VPC própria, uma sub-rede pública, sem NAT, endpoint de gateway para o S3
# (gratuito) e nenhuma porta de entrada. Administração só pelo SSM Session Manager.

data "aws_region" "current" {}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

# O grupo padrão fica sem regras, para não ser usado por engano.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-default-sem-regras" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = var.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.cidr_block, 1, 0)
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  tags              = { Name = "${var.name}-s3" }
}

resource "aws_security_group" "collector" {
  name        = "${var.name}-collector"
  description = "Coletor - sem entrada; saida apenas HTTPS"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name}-collector" }
}

# DNS da VPC, Amazon Time Sync e metadados da instância usam endereços link-local, que não
# passam pelo security group.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.collector.id
  description       = "HTTPS para Binance, SSM, CloudWatch, S3, GitHub e repositorios"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
