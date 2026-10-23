# Infraestrutura AWS (Terraform)

Implementa o doc 03 §7 a §10: região Ohio, us-east-2 (DEC-26), Terraform (DEC-22), ADR-05 a ADR-12.

A conta do projeto só aceita a us-east-2 (SCP da organização gerenciada pela AWS). De lá, `api.binance.com` e `stream.binance.com` respondem 451; o coletor usa só `data-stream.binance.vision` e `data-api.binance.vision` (doc 06 §2.4).

```text
infra/
  bootstrap/            bucket do estado do Terraform (estado local; roda uma vez)
  envs/prod/            ambiente de produção (estado no S3, bloqueio nativo)
  modules/
    network/            VPC, sub-rede pública, IGW, endpoint S3, security group sem entrada
    data_bucket/        bucket privado, versionado, criptografado, só HTTPS, prevent_destroy
    collector_host/     IAM, EC2 t4g, script de inicialização, systemd, trader-deploy, alarmes
    observability/      grupos de log, SNS por e-mail, orçamento mensal
```

## Pré-requisitos

- Terraform ≥ 1.10 (`brew install hashicorp/tap/terraform`).
- AWS CLI v2 com o perfil **`trader`** apontando para a conta **019419901344**, criado com `aws login --profile trader --region us-east-2` (a região do login precisa ser a do projeto). **Nunca use o perfil `default`: ele é outra conta, sem créditos.** O provider recusa qualquer conta diferente (`allowed_account_ids`).
  - Opções: `aws login --profile trader` (credenciais temporárias pelo console) ou IAM Identity Center (`aws configure sso --profile trader`).
  - Conferir: `aws sts get-caller-identity --profile trader` deve mostrar `019419901344`.
- Plugin do Session Manager para abrir terminal no host: `brew install --cask session-manager-plugin`.
- `terraform.tfvars` em `bootstrap/` e `envs/prod/` (modelos `*.tfvars.example`; os reais ficam fora do Git).

## Primeira implantação (doc 03 §9.4)

Nenhum `apply` sem revisar o `plan` (RN-012).

1. **Estado do Terraform**
   ```bash
   cd infra/bootstrap && terraform init && terraform plan -out bootstrap.tfplan
   terraform apply bootstrap.tfplan
   ```
2. **Ambiente**
   ```bash
   cd infra/envs/prod && terraform init && terraform plan -out prod.tfplan
   terraform apply prod.tfplan
   ```
3. **Confirmar o e-mail** que a AWS envia para a assinatura do SNS.
4. **Publicar o código** (árvore Git limpa): `scripts/publish-artifact.sh` → imprime a versão, por exemplo `0.1.0.dev0-7e6d2a6`.
5. **Instalar sem iniciar e testar a conectividade** a partir da us-east-2:
   ```bash
   $(terraform -chdir=infra/envs/prod output -raw ssm_session)
   sudo trader-deploy <versão>
   /opt/trader/current/bin/trader smoke --config /etc/trader/trader.toml --duration 600
   ```
   Avisos de 451 em `api.binance.com` e `historicalTrades` são esperados. Falha nos endpoints `*.binance.vision` impede a coleta: plano B (DEC-26).
6. **Iniciar a coleta**: `sudo trader-deploy <versão> --start` e conferir com
   `sudo -u trader /opt/trader/current/bin/trader status --config /etc/trader/trader.toml`.
7. **Ligar o pipeline**: `sudo systemctl enable --now trader-pipeline.timer`.
8. **Conferir as dimensões das métricas**:
   `aws cloudwatch list-metrics --namespace Trader --profile trader --region us-east-2`.
   Os alarmes esperam `InstanceId` + `metric_type=gauge` nas métricas StatsD e `InstanceId`, `path=/`, `fstype=xfs` no disco. Se forem outras, ajustar `statsd_dimensions` em `modules/collector_host/main.tf`. Conferido em 23/10/2026: as 6 métricas da aplicação, memória e disco chegam com essas dimensões.
9. **Ligar os alarmes da aplicação**: `app_alarms_enabled = true` no `terraform.tfvars`, `plan` e `apply`.
10. Início da contagem do CA-01 (7 dias contínuos).

## Operação

| Tarefa | Como |
|---|---|
| Terminal no host | `$(terraform -chdir=infra/envs/prod output -raw ssm_session)` |
| Nova versão | commit → `scripts/publish-artifact.sh` → `sudo trader-deploy <versão>` (troca de cor a/b sem lacuna, doc 03 §9.2) |
| Voltar versão | `sudo trader-deploy <versão anterior>` |
| Logs | CloudWatch Logs `/trader/collector` e `/trader/pipeline`; no host, `/var/log/trader/` |
| Pipeline manual | `sudo systemctl start trader-pipeline.service` |

Mudanças no script de inicialização (`user_data`) não recriam a instância (`ignore_changes`): aplicar no host pelo SSM ou recriar o host de propósito. A instância tem proteção contra encerramento, e os buckets têm `prevent_destroy`.

## Custos (doc 03 §10)

| Período | Instância | US$/mês |
|---|---|---|
| Até 31/12/2026 | t4g.small (teste gratuito) | 1,5 – 5,3 |
| 01/01 – 12/04/2027 | t4g.small / t4g.micro | 13,9 – 18,0 / 7,8 – 11,9 |
| Depois dos créditos | t4g.micro / t4g.small | ~13 / ~19 (teto R$ 100 ≈ US$ 20) |

O orçamento (`monthly_budget_usd`, padrão US$ 20) mede o custo **bruto**, sem descontar créditos, para acompanhar o consumo deles; avisa por e-mail em 50%, 80% e 100%, e quando a previsão passa de 100%.
