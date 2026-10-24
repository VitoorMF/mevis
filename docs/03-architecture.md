# 03 — System Architecture Document

| Campo | Valor |
|---|---|
| Projeto | **MERIS** — Market Evidence & Research Intelligence System |
| Documento | 03 — System Architecture |
| Versão | 0.10 — rascunho |
| Escopo desta versão | **Data Engine (V0.1) e hospedagem na AWS.** V0.2 e V0.3 apenas esboçadas (§13). |
| Status | DEC-22 a DEC-24 e DEC-26 tomadas (DEC-21 substituída); ADR-01 a ADR-12 **aprovadas** (DEC-24) |
| Data | 23/10/2026 |
| Documentos de origem | [01 — PRD](01-prd.md) v0.6; [02 — SRS](02-srs.md) v0.3; [06 — Data & Integrations](06-data-integrations.md) v0.2 |

---

## 0. Escopo

Este documento define **como** o sistema é construído e onde roda: estilo arquitetural, processos, módulos, fluxos, infraestrutura AWS, segurança, custos, falhas e implantação. O **o quê** está no SRS; os contratos de dados estão no doc 06.

Preços foram consultados na AWS Price List API em 23/10/2026 (região us-east-2, salvo indicação) e podem mudar.

---

## 1. Decisões

### 1.1 Tomadas com o responsável pelo produto (23/10/2026)

| ID | Tema | Decisão | Resolve |
|---|---|---|---|
| DEC-21 | Região | **Tóquio (`ap-northeast-1`)**: amplamente relatada como a região dos servidores da Binance, com custo próximo ao das regiões mais baratas. Frankfurt (`eu-central-1`) fica como alternativa caso a conectividade falhe (R-18). | PD-04 (região) — **substituída por DEC-26** |
| DEC-22 | Infraestrutura como código | **Terraform.** | — |
| DEC-23 | Plano depois dos créditos | t4g.micro sob demanda (~US$ 13/mês em us-east-2), dentro do teto de **R$ 100/mês** (DEC-16, revisto em 23/10/2026). O CP-3 (§10.4) confirma com consumo medido e avalia se a V0.3 exige t4g.small. | Parte de DEC-16 |
| DEC-24 | Arquitetura | ADR-01 a ADR-12 abaixo aprovadas. | PD-12, PD-13 |
| DEC-26 | Região | **Ohio (`us-east-2`)**: a única que a conta aceita (§2.2). Coleta só pelos endpoints `*.binance.vision`. Tóquio fica como plano B, exigindo plano pago e recursos avançados. | Substitui DEC-21 |

### 1.2 Decisões de arquitetura (aprovadas em 23/10/2026 — DEC-24)

| ID | Proposta | Seção |
|---|---|---|
| ADR-01 | Monólito modular em Python, executado como poucos processos no mesmo host. | §3 |
| ADR-02 | Processos se comunicam por arquivos brutos fechados e por um estado operacional em SQLite; sem filas nem brokers. | §4.2 |
| ADR-03 | Coletor enxuto: sem SDK da AWS e sem bibliotecas de Parquet; métricas via StatsD para o agente do CloudWatch. | §4.1 |
| ADR-04 | Normalização em lote horário, portável (EC2, MacBook ou Lambda). | §6.3 |
| ADR-05 | Implantação por artefato versionado no S3, com troca de versão do coletor sem lacuna. | §9 |
| ADR-06 | Rede mínima: VPC própria, uma sub-rede pública, sem NAT, endpoint de gateway para o S3, sem portas de entrada, administração pelo SSM. | §7.2 |
| ADR-07 | Créditos de CPU da instância no modo `standard` (sem cobrança por excesso). | §7.3 |
| ADR-08 | A instância não tem permissão para apagar objetos no S3; retenção só por regras de ciclo de vida. | §8 |
| ADR-09 | Conta AWS dedicada (a conta nova, com créditos) e proteções contra uso da conta errada. | §7.1 |
| ADR-10 | Estado do Terraform no S3, com bloqueio nativo, criado por uma configuração de *bootstrap* separada. | §7.6 |
| ADR-11 | (Preliminar) O Trading Engine da V0.3 terá conexão de mercado própria, sem depender do coletor. | §13 |
| ADR-12 | Alertas por e-mail via SNS (resolve PD-13). | §7.5 |

---

## 2. Fatores que orientam a arquitetura

### 2.1 Atributos de qualidade, em ordem de prioridade

1. **Durabilidade dos dados** — o livro de ofertas não pode ser recuperado depois (DEC-18).
2. **Disponibilidade da coleta** — cada minuto parado é um minuto a menos de livro.
3. **Custo** — créditos limitados e teto de R$ 100/mês depois deles (DEC-16).
4. **Simplicidade** — um desenvolvedor, dedicação parcial (RS-08, PP-07).
5. **Observabilidade e auditabilidade** (PP-06).
6. **Segurança** — sem credenciais da exchange; privilégio mínimo na nuvem.

Latência **não** é prioridade: o horizonte é de minutos (DEC-15).

### 2.2 Restrições da conta AWS

| Fato | Fonte | Consequência |
|---|---|---|
| A conta configurada hoje no AWS CLI do MacBook é a **724772086347**, criada em 07/09/2024, no modelo antigo de Free Tier e com recursos de outros projetos. | Consulta somente leitura em 23/10/2026 | **Não é a conta dos créditos.** Nada deste projeto pode ser criado nela (ADR-09). |
| Os créditos (US$ 180) estão numa **conta nova, no plano gratuito, que expira em 12/04/2027**. | Responsável pelo produto | Criação estimada por volta de 12/10/2026. |
| Quando o plano gratuito expira, a conta é fechada; há 90 dias para passar ao plano pago antes da exclusão permanente de recursos e dados. | Documentação AWS | Risco R-19; checkpoint CP-2 obrigatório. |
| O plano gratuito também termina se os créditos acabarem. | Documentação AWS | Alertas de orçamento (§10.5). |
| Créditos valem 12 meses a partir da criação da conta (~10/2027). | Documentação AWS; [A VERIFICAR no console] | Horizonte máximo dos créditos. |
| Instâncias permitidas no plano gratuito: t3.micro, t3.small, t4g.micro, t4g.small, c7i-flex.large, m7i-flex.large. | Documentação AWS | t4g.nano e Savings Plans só no plano pago. |
| Teste gratuito do t4g.small: 750 h/mês até 31/12/2026, para clientes novos e antigos. | Documentação AWS; [A VERIFICAR na fatura de novembro] | Instância inicial sem custo de computação. |
| IPv4 público: 750 h/mês grátis nos primeiros 12 meses para clientes do Free Tier de EC2. | Documentação AWS; [A VERIFICAR na fatura de novembro] | Pode zerar US$ 3,65/mês. |
| Binance sem endereço IPv6 (sem registro AAAA). | DNS, 23/10/2026 | IPv4 público é obrigatório (sem NAT, que custaria mais). |
| A conta pertence a uma organização gerenciada pela AWS (nova experiência), cuja SCP nega chamadas fora da **us-east-2**; testadas e negadas: us-east-1, us-west-2, ca-central-1, sa-east-1, eu-central-1, eu-west-1, ap-northeast-1 e ap-southeast-1. | `terraform plan` e chamadas de leitura, 23/10/2026 | DEC-26. Outras regiões só depois de passar ao plano pago, ativar os recursos avançados (irreversível) e editar a SCP `RegionFloor`. |
| Da us-east-2, `api.binance.com` e `stream.binance.com` respondem HTTP 451; `data-api.binance.vision`, `data-stream.binance.vision` e `data.binance.vision` respondem. | CloudShell, 23/10/2026 (doc 06 MD-16 a MD-21) | O host usa só `*.binance.vision`; lacunas de negócios só pelos arquivos diários. |

---

## 3. Estilo arquitetural (ADR-01)

**Proposta:** um único pacote Python, organizado em módulos com fronteiras explícitas, implantado como poucos processos independentes no mesmo host e orquestrados pelo `systemd`.

| Alternativa | Vantagens | Por que não agora |
|---|---|---|
| **Monólito modular + processos systemd** (proposta) | Simples de construir, testar e implantar; quase sem custo extra de memória; fronteiras de módulo permitem separar depois. | — |
| Microsserviços / contêineres (ECS, Docker) | Isolamento e escala independentes. | Custo de memória e operação sem benefício para um par, um host e uma pessoa. |
| Serverless (Lambda) para tudo | Sem servidor para manter. | WebSocket de longa duração não cabe em Lambda; serviria só para o lote horário (§6.3). |
| Banco de dados time-series | SQL ao vivo. | Rejeitado em DEC-20. |

Regra de dependência entre módulos (estilo hexagonal): o núcleo (modelos, normalização e regras de qualidade) não depende de Binance, AWS ou disco; os adaptadores ficam nas bordas. Isso permite testar o núcleo isoladamente (RNF-019) e trocar de fonte ou de nuvem sem reescrevê-lo (R-13).

---

## 4. Visão de execução

### 4.1 Processos no host de coleta

```text
┌──────────────────────────── EC2 t4g (Amazon Linux 2023, ARM64) ─────────────────────────────┐
│                                                                                              │
│  trader-collector (serviço, sempre ativo)          trader-pipeline (timer, HH:05 UTC)        │
│   ├─ cliente WebSocket + rotação de sessão          ├─ recupera lacunas (REST e arquivo D+1) │
│   ├─ gravador bruto (fsync a cada 1 s)              ├─ comprime e verifica arquivos fechados │
│   ├─ verificações ao vivo (ids, U/u, silêncio)      ├─ replica no S3                         │
│   ├─ snapshots REST do livro, offset de relógio     ├─ normaliza a hora → Parquet → S3       │
│   └─ StatsD → agente CloudWatch (métricas, vida)    ├─ máscara de qualidade e datasets oper. │
│                                                     └─ retenção local (só o já replicado)    │
│                                                                                              │
│  amazon-cloudwatch-agent (métricas StatsD, memória, disco; envio de logs)                    │
│  amazon-ssm-agent (acesso administrativo)    chronyd (Amazon Time Sync)                      │
│                                                                                              │
│  /var/lib/trader/  raw/  normalized/  quarantine/  state.db (SQLite)  health/                │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Processo | Tipo | Responsabilidade | Requisitos |
|---|---|---|---|
| `trader-collector` | Serviço `systemd` (`Restart=always`) | Conexão, rotação, gravação bruta, verificações ao vivo, snapshots REST, offset, métricas e sinal de vida. | RF-101–RF-109, RF-111–RF-119, RF-121–RF-125, RF-131, RF-135, RF-161, RF-192–RF-194 |
| `trader-pipeline` | Timer horário | Recuperação de lacunas (§6.4), compressão, replicação, normalização, máscara de qualidade, exportação de datasets operacionais, retenção local. | RF-126–RF-129, RF-131–RF-139, RF-145–RF-149, RF-151–RF-159 |
| `trader` (CLI) | Sob demanda | Status, backfill, relatório, reconstrução, replay. | RF-181–RF-185 |

**Coletor enxuto (ADR-03).** O coletor não importa SDK da AWS nem bibliotecas de Parquet. Ele grava bytes e faz verificações leves; métricas e sinal de vida saem por pacotes UDP StatsD para o agente do CloudWatch. Com isso a memória fica baixa (RNF-008), e a instância pode ser reduzida depois (DEC-23).

**Proteção do coletor.** O coletor recebe prioridade no OOM killer (`OOMScoreAdjust` negativo). O pipeline roda com limite de memória (`MemoryMax`) e pode ser morto e reexecutado sem perda, porque é idempotente.

### 4.2 Comunicação entre processos (ADR-02)

| Canal | Produtor → consumidor | Contrato |
|---|---|---|
| Arquivos brutos | coletor → pipeline | Arquivo aberto tem sufixo `.open`; ao fechar, é renomeado atomicamente para `.jsonl` (doc 06 §5.2). O pipeline só lê arquivos fechados. |
| `state.db` (SQLite, WAL) | todos | Estado operacional mutável: sessões, lacunas e seus status, fila de recuperação, controle de replicação e marcas d'água das verificações (permitem detectar lacunas entre execuções do coletor). Exportado a cada hora para os datasets operacionais (doc 06 §5.4) e copiado para o S3. |
| Arquivos de saúde (`health/`) | coletor → CLI e implantação | JSON reescrito a cada 5 s com a idade da última mensagem por canal e a versão em execução. |
| StatsD (UDP local) | coletor → agente CloudWatch | Métricas agregadas a cada 60 s (§7.5). |

Sem filas, brokers ou HTTP interno. Um processo que cai não derruba os outros, e todo estado sobrevive a reinícios.

---

## 5. Módulos do pacote

```text
trader/
  core/        modelos canônicos, unidades de tempo, decimais          (sem E/S)
  normalize/   parsers por canal → registros canônicos                 (sem E/S; mesmo código ao vivo e em lote)
  quality/     regras de validação, detecção de lacunas, máscara       (sem E/S)
  sources/     adaptadores Binance: WebSocket, REST + limitador de peso, arquivos públicos
  ingest/      sessão e rotação, gravador bruto, verificações ao vivo, snapshots, offset
  storage/     layout de caminhos, escrita de Parquet, sincronização S3, retenção
  recovery/    estratégias de recuperação por canal
  pipeline/    rodada horária: órfãos, compressão, normalização por hora, máscara, exportações,
               réplica e retenção; backfill dos arquivos públicos (doc 06 §7);
               relatório de qualidade (doc 06 §8.3)
  ops/         configuração, logs estruturados, StatsD, saúde, estado SQLite
  cli/         comandos
```

| Tecnologia [PROPOSTA] | Uso | Observação |
|---|---|---|
| Python 3.12, gerenciado por `uv`, com `uv.lock` | Linguagem e ambiente reprodutível | O Python do sistema (3.9 no MacBook e no Amazon Linux) não é usado (RNF-021). |
| `asyncio` + `websockets` | Cliente WebSocket | Já usado nas medições do doc 06. |
| `httpx` | Cliente REST | Com o limitador de peso próprio (doc 06 §3.5). |
| `zstandard` | Compressão | Só no pipeline. |
| `pyarrow` | Parquet com `DECIMAL` | Só no pipeline e na pesquisa. |
| `boto3` | S3 | Só no pipeline e na CLI. No MacBook, `botocore[crt]` (só desenvolvimento) permite usar as credenciais de `aws login`. |
| `sqlite3` (biblioteca padrão) | Estado operacional | Sem servidor. |
| DuckDB, Polars | Leitura na pesquisa (MacBook) | DEC-20. |
| `pytest`, `ruff`, `mypy` | Testes e qualidade | RNF-020. |

---

## 6. Fluxos principais

### 6.1 Mensagem ao vivo

1. O socket entrega a mensagem; o coletor lê o relógio (`recv_ts_us`).
2. Acrescenta a linha ao arquivo bruto aberto do canal e da sessão (fsync a cada 1 s).
3. Faz a verificação leve do canal: sequência de ids (`trade`), `U`/`u` (diferencial), atualização do relógio monotônico de silêncio.
4. Em anomalia, registra a lacuna no `state.db` e incrementa as métricas.

### 6.2 Rotação planejada da conexão (RF-115)

1. Aos 23 h de idade, abre uma nova sessão com os mesmos streams. Enquanto a nova "aquece", tudo o que ela recebe vai para a camada bruta e para um buffer; as verificações ao vivo continuam usando só a sessão antiga.
2. Quando todos os canais da nova sessão recebem dados, espera um período de sobreposição (2 s) para garantir que a antiga já processou o que a nova tem no buffer.
3. Reprocessa o buffer nas verificações (as duplicatas da sobreposição são ignoradas pelos rastreadores), promove a nova sessão e fecha a antiga.
4. As duas sessões gravam em arquivos separados durante a sobreposição; a deduplicação dos dados acontece na normalização.

Se a nova sessão falhar ou não ficar pronta em 60 s, a rotação é adiada em 1 minuto. Se a antiga morrer durante o aquecimento, a nova assume imediatamente.

### 6.3 Pipeline horário (ADR-04)

```text
HH:05 ─► fechar órfãos ─► recuperar lacunas ─► comprimir e verificar arquivos fechados ─► registrar no state.db
         para cada hora cujas entradas mudaram:
           normalizar ─► deduplicar ─► validar ─► Parquet + quarentena ─► máscara de qualidade
         exportar datasets operacionais e cópia do state.db
         replicar o que falta ─► aplicar retenção local
```

- **Órfãos:** um arquivo `.open` é fechado pelo pipeline se a sessão consta como encerrada ou se a hora dele terminou há mais de 5 minutos (o coletor vivo fecha arquivos na virada da hora).
- **Quando uma hora é normalizada:** quando nenhum arquivo bruto dela está aberto. Lê a hora H e 5 minutos de margem das horas vizinhas, em ordem global de recebimento entre sessões, e particiona pelo horário do evento (negócios, candles) ou do primeiro recebimento (livro).
- **Renormalização automática:** um resumo das entradas (arquivos brutos da hora anterior, da própria hora e da seguinte; lacunas e offsets de relógio próximos) fica no `state.db`. Se mudar — arquivo vizinho que fechou depois, lacuna detectada mais tarde, recuperação —, a hora é refeita. `trader pipeline --rebuild` refaz todas (RF-133), desde que as entradas ainda estejam no disco local.
- **Idempotente:** cada etapa consulta o `state.db`; reexecutar não duplica nada.
- **Exclusivo:** um `flock` impede duas execuções simultâneas.
- **Memória limitada:** em fluxo; só as chaves já vistas ficam em memória, e o Parquet é gravado em blocos. Uma hora real de teste usou ~125 MB.
- **Réplica:** interface de armazenamento com implementação em diretório local (desenvolvimento); a implementação no S3 entra com a infraestrutura AWS.
- **Portável:** lê arquivos brutos (locais ou do S3) e escreve no S3. Pode rodar no MacBook ou em Lambda, caso a instância seja reduzida (DEC-23).

### 6.4 Recuperação de lacunas

A recuperação é a primeira etapa do pipeline horário. Ela lê as lacunas abertas no `state.db`, aplica a estratégia do canal (doc 06 §9.1), grava o que obteve na camada bruta (canais `rest:klines` e `archive:trades`) e atualiza o status. O arquivo bruto novo faz a hora ser normalizada de novo na mesma rodada. Lacunas de livro já nascem irrecuperáveis.

[PROPOSTA, 24/10/2026] Os timers separados previstos antes (`trader-recovery` a cada 5 min, para a REST, e `trader-archive` diário) foram trocados por essa etapa: no host de produção a REST de negócios não responde (doc 06 MD-18), um candle perdido pode esperar até a próxima hora sem prejuízo para a pesquisa, e fica um processo a menos (ADR-01, PP-07). Se a V0.3 precisar de recuperação mais rápida, um timer próprio chama a mesma etapa.

---

## 7. Infraestrutura AWS

### 7.1 Conta (ADR-09)

- **Conta:** somente a conta nova, com créditos: **019419901344**, confirmada pelo responsável em 23/10/2026. Perfil do AWS CLI: `trader`.
- **Proteções contra a conta errada:**
  - perfil dedicado do AWS CLI (por exemplo, `trader`), separado do perfil padrão, que hoje aponta para 724772086347;
  - `allowed_account_ids` no provider do Terraform, que recusa qualquer outra conta;
  - tags padrão em todos os recursos: `Project=trader`, `Env=prod`, `ManagedBy=terraform`.
- **Usuário raiz:** MFA ativa e nenhum uso no dia a dia. O dia a dia usa uma identidade com credenciais temporárias (IAM Identity Center ou `aws login`).

### 7.2 Rede (ADR-06)

| Recurso | Configuração |
|---|---|
| VPC | Dedicada, `/24`, uma zona de disponibilidade. |
| Sub-rede | Uma, pública, com atribuição automática de IPv4 público. |
| Internet Gateway | Saída para Binance, SSM e CloudWatch. |
| NAT Gateway | **Não usar** — custaria mais que a instância. |
| Endpoint de gateway S3 | Sim (gratuito): tráfego para o S3 sem passar pela internet. |
| Endpoints de interface | Não (custo por hora). |
| Security group | **Nenhuma regra de entrada.** Saída TCP 443. |
| Acesso administrativo | SSM Session Manager; sem par de chaves SSH. |

### 7.3 Host de coleta

| Item | Configuração | Motivo |
|---|---|---|
| Tipo inicial | **t4g.small** (2 vCPU ARM, 2 GiB) | Gratuito até 31/12/2026; folga para medir antes de reduzir (CP-1). |
| Tipos admitidos | t4g.micro (plano gratuito); t4g.nano (só no plano pago) | DEC-23. Mesma arquitetura ARM64 do MacBook. |
| Sistema | Amazon Linux 2023 ARM64 | SSM e chrony pré-configurados; sem custo de licença. |
| Créditos de CPU | Modo `standard` (ADR-07) | Uso acima da linha de base é limitado, nunca cobrado. Alarme de saldo de créditos. |
| Disco | gp3, 16 GB, criptografado | Inclui swap de 1 GB e espaço para ~30 dias de buffer se a replicação falhar. |
| Metadados | IMDSv2 obrigatório, limite de saltos 1 | RNF-014. |
| Proteções | Contra encerramento acidental; recuperação automática da instância | §11. |
| Relógio | chrony com Amazon Time Sync | RNF-012. Corrige o problema medido no MacBook (doc 06 MD-14). |

**Orçamento de memória** [PROPOSTA, a medir até CP-1]: sistema e agentes ~350 MB; coletor ≤ 300 MB; pipeline ≤ 600 MB (limitado pelo `systemd`). Cabe com folga em 2 GiB; em 1 GiB (micro) exige o pipeline limitado a ~350 MB, e o swap fica como reserva.

### 7.4 Armazenamento

| Recurso | Configuração |
|---|---|
| Bucket de dados `trader-data-<conta>-<região>` | Bloqueio de acesso público total; criptografia SSE-S3; versionamento ativo; política que nega acesso sem TLS. |
| Prefixos | `raw/`, `normalized/`, `quarantine/`, `ops/` (cópias do `state.db`), `artifacts/` (versões do software). |
| Ciclo de vida | Versões não atuais expiram em 30 dias; uploads multipartes incompletos são abortados em 7 dias. Transição para classe mais barata: avaliar em CP-3. |
| Bucket de estado do Terraform | Separado, privado, versionado (ADR-10). |

### 7.5 Observabilidade e alertas (ADR-12)

**Métricas personalizadas (≤ 10, dentro da camada gratuita):**

| # | Métrica | Origem | Alarme |
|---|---|---|---|
| 1 | `Heartbeat` | Coletor (StatsD) | Ausente por 3 min → alarme (dados ausentes contam como violação): sinal de vida externo, RF-164. |
| 2 | `MaxChannelAgeSeconds` | Coletor | > 30 s por 3 min. |
| 3 | `GapsDetected` | Coletor | > 5 por hora [PROPOSTA]. |
| 4 | `ClockOffsetMs` (absoluto) | Coletor | > 250 ms. |
| 5 | `ReplicationLagMinutes` | Pipeline | > 90 min. |
| 6 | `MessagesPerMinute` | Coletor | — (diagnóstico). |
| 7 | `disk_used_percent` | Agente | > 80%. |
| 8 | `mem_used_percent` | Agente | — (dimensionamento em CP-1). |

**Métricas nativas do EC2 (gratuitas):** `StatusCheckFailed_System` aciona a recuperação automática; `StatusCheckFailed_Instance` aciona reinicialização; `CPUCreditBalance` baixo gera alarme. Total: 9 alarmes, dentro dos 10 gratuitos.

**Logs:** JSON estruturado, enviados pelo agente para o grupo `/trader/*`, com retenção de 14 dias. Meta de volume: < 1 GB/mês (a camada gratuita cobre 5 GB). Detalhes por canal ficam nos logs, não em métricas.

**Alertas:** tópico SNS `trader-alerts` com assinatura por e-mail (camada gratuita de 1000 e-mails/mês).

**Custos:** AWS Budgets (gratuito sem ações) com alertas por e-mail em 50%, 80% e 100% de US$ 20/mês, e alerta de previsão; mais os alertas nativos do plano gratuito. O orçamento mede o custo **bruto**, sem descontar créditos, para mostrar o ritmo de consumo deles.

**Dimensões das métricas [A VERIFICAR na primeira implantação]:** os alarmes esperam `InstanceId` + `metric_type=gauge` nas métricas StatsD (o agente publica com `append_dimensions` de `InstanceId`) e `InstanceId`, `path=/`, `fstype=xfs` no disco. Se divergirem, o alarme de sinal de vida dispara de imediato, o que expõe o erro (runbook em `infra/README.md`, passo 8). As ações dos alarmes da aplicação ficam desligadas (`app_alarms_enabled`) até a coleta começar.

### 7.6 Terraform (DEC-22, ADR-10)

```text
infra/
  bootstrap/          bucket de estado (estado local; executado uma vez)
  envs/prod/          provider (us-east-2, allowed_account_ids, default_tags), backend S3
  modules/
    network/          VPC, sub-rede, IGW, rotas, endpoint S3, security group
    data_bucket/      bucket de dados, políticas, ciclo de vida
    collector_host/   papel IAM, perfil de instância, EC2, user data, alarmes da instância
    observability/    grupos de log, SNS, alarmes, orçamento
```

- Versões do Terraform e do provider fixadas (`.terraform.lock.hcl`); backend S3 com `use_lockfile = true` (Terraform ≥ 1.10), sem DynamoDB.
- A região é variável, para permitir a troca para Frankfurt sem reescrever nada (R-18).
- `terraform plan` revisado pelo responsável pelo produto antes de qualquer `apply` (RN-012).

---

## 8. Segurança

| Controle | Implementação | Requisito |
|---|---|---|
| Sem credenciais da exchange | Apenas endpoints públicos. | RN-001, RNF-013 |
| Credenciais temporárias no host | Perfil de instância; nenhuma chave gravada. | RNF-014 |
| Privilégio mínimo | Papel da instância: `s3:PutObject`/`GetObject` nos prefixos do projeto; `s3:ListBucket` no bucket; **sem `s3:DeleteObject`** (ADR-08); `logs:PutLogEvents` nos grupos `/trader/*`; `cloudwatch:PutMetricData` restrito ao namespace `Trader`; política gerenciada do SSM. | RNF-014, RN-007 |
| Sem portas de entrada | Security group sem regras de entrada; acesso só via SSM. | RNF-015 |
| Dados criptografados e privados | EBS criptografado; SSE-S3; bloqueio de acesso público; TLS obrigatório. | RNF-017 |
| Segredos | Nenhum segredo na V0.1. A assinatura de e-mail do SNS não é segredo. | RNF-016 |
| Imutabilidade | Instância sem permissão de apagar; versionamento do bucket protege contra sobrescrita. | RN-007 |
| Conta certa | ADR-09. | R-20 |

---

## 9. Implantação (ADR-05)

### 9.1 Artefato e instalação

1. No MacBook, com a árvore Git limpa: `scripts/publish-artifact.sh` gera a wheel, um `requirements.txt` com hashes (exportado do `uv.lock`) e um `SHA256SUMS`, e envia para `s3://…/artifacts/<versão do pacote>-<commit>/`.
2. No host, via SSM: `sudo trader-deploy <versão>` baixa o artefato, confere os hashes, cria um ambiente novo em `/opt/trader/releases/<versão>` (Python 3.12 gerenciado pelo `uv`, instalação com `--require-hashes`) e troca o link `current` de forma atômica.
3. Sem coletor ativo, só instala; com `--start`, ou se já houver coletor ativo, faz a troca sem lacuna (§9.2).
4. Rollback: `sudo trader-deploy <versão anterior>`.

O host não precisa de acesso a repositório Git nem de credenciais de terceiros.

### 9.2 Troca do coletor sem lacuna

Unidades modelo `trader-collector@a` e `trader-collector@b`:

1. Inicia a cor ociosa com a nova versão (`trader collect --instance <cor>`).
2. Espera o arquivo de saúde da cor nova (`health/collector-<cor>.json`) mostrar todos os canais recebendo, por até 120 s.
3. Para e desabilita a cor antiga; habilita a nova. Se a nova não ficar pronta, ela é parada e a antiga continua.

Durante a sobreposição, as duas gravam em sessões distintas (doc 06 §3.3), e a deduplicação acontece na normalização. Os demais processos só trocam o link `current`.

A cor nova não compara os primeiros dados com as marcas d'água gravadas no estado se outra instância estiver ativa (saúde `running` atualizada há menos de 3 intervalos de saúde): essas marcas ficam alguns segundos para trás e gerariam lacunas falsas, já que a antiga continua gravando até a troca. Pelo mesmo motivo, a cor antiga não regrava as marcas ao parar se a nova estiver ativa. As 3 lacunas falsas da primeira troca (23/10/2026, 23:19 UTC) foram marcadas como `recovered` à mão, com origem na sessão da cor antiga, que tinha os dados; o estado anterior ficou em `ops/state-before-gap-fix.db`.

### 9.3 Inicialização do host

O `user data` (cloud-init) instala o `uv` e o agente do CloudWatch (StatsD local, memória, disco e envio dos logs), cria o usuário de serviço, os diretórios e um swap de 1 GB, grava `/etc/trader/trader.toml`, instala as unidades `systemd` (`trader-collector@.service`, `trader-pipeline.service` e `.timer` às HH:05 UTC), o `logrotate` e o `trader-deploy`. **Não** inicia o coletor: isso só acontece depois do teste de fumaça (§9.4).

### 9.4 Sequência da primeira implantação

1. **Bootstrap** do estado do Terraform.
2. **Ambiente completo** (rede, bucket, observabilidade, host), com as ações dos alarmes da aplicação desligadas.
3. **Teste de fumaça:** `trader-deploy <versão>` (só instala) e `trader smoke` (10 min de WebSocket em cada endpoint principal, checagem curta dos alternativos, REST com ida e volta e offset, `historicalTrades` sem chave). Na us-east-2, `api.binance.com` e `historicalTrades` saem como **aviso** (451 esperado). Falha nos endpoints `*.binance.vision` impede a coleta: plano B (DEC-26).
4. Coletor (`trader-deploy <versão> --start`) e timer do pipeline; conferência das dimensões das métricas; alarmes ligados. Início da contagem de CA-01 (7 dias).

O passo a passo com comandos está em `infra/README.md`.

---

## 10. Custos e créditos

### 10.1 Preços de referência (us-east-2, 730 h/mês)

| Item | US$/mês |
|---|---|
| t4g.small / t4g.micro / t4g.nano | 12,26 / 6,13 / 3,07 |
| gp3, 16 GB | 1,28 |
| IPv4 público | 3,65 (ou 0 se houver franquia de 750 h) |
| S3 Standard (US$ 0,023/GB-mês) | ~0,17 no 1º mês → ~1,96 no 12º (doc 06 §6.3) |
| CloudWatch, SNS, SSM, Budgets, endpoint S3 | 0 dentro das camadas gratuitas |
| Transferência para o MacBook | 0 até 100 GB/mês |

### 10.2 Custo mensal por fase

| Fase | Instância | US$/mês |
|---|---|---|
| Até 31/12/2026 | t4g.small (teste gratuito) | 1,5 – 5,3 |
| 01/01/2027 – 12/04/2027 | t4g.small | 13,9 – 18,0 |
| | t4g.micro | 7,8 – 11,9 |
| Após os créditos (estimativa no 12º mês) | t4g.small | ~19 |
| | t4g.micro | ~13 |
| | t4g.nano (plano pago) | ~10 |

Faixas: com e sem franquia de IPv4; crescimento do S3. Referência do teto: R$ 100 ≈ US$ 20 (câmbio de ~R$ 5,0 em 07/10/2026). Depois dos créditos, t4g.micro cabe com folga; t4g.small fica no limite.

### 10.3 Consumo dos créditos (US$ 180)

| Cenário | Gasto até 12/04/2027 | Saldo na passagem ao plano pago | Até a expiração dos créditos (~10/2027) |
|---|---|---|---|
| A — micro a partir de jan/2027, IPv4 grátis | ~30 | ~150 | Sobram créditos: dá para manter o t4g.small. |
| B — small o tempo todo, IPv4 pago | ~71 | ~109 | Créditos acabam por volta de set/2027. |

Conclusão: a escolha da instância para 2027 depende de dois fatos que a fatura de novembro vai mostrar (franquia de IPv4 e teste do t4g.small no plano gratuito) e do consumo medido. Por isso existe o CP-1.

### 10.4 Checkpoints

| ID | Quando | Decisão |
|---|---|---|
| **CP-1** | 15/12/2026 | Tamanho da instância de jan a abr/2027, com base em memória e CPU medidas e na fatura de novembro. |
| **CP-2** | Até 15/03/2027 — **prazo rígido** | Passar a conta ao plano pago antes de 12/04/2027 (sem isso, a conta fecha e os dados são apagados em 90 dias — R-19). Preparar a estratégia pós-créditos. |
| **CP-3** | Créditos ≤ 30% ou 30 dias antes de expirarem | Confirmar o plano pós-créditos (DEC-23): t4g.micro dentro do teto de R$ 100; avaliar classe mais barata no S3 e se a V0.3 exige t4g.small (que ficaria ligeiramente acima do teto). |

### 10.5 Proteções de custo

- Alertas do AWS Budgets e do plano gratuito.
- Créditos de CPU `standard`.
- Sem NAT e sem endpoints de interface.
- Endpoint de gateway S3.
- Métricas e alarmes dentro da camada gratuita.
- Tags de projeto em tudo.
- Nenhum `apply` sem plano revisado (RN-012).

---

## 11. Falhas e recuperação

| Falha | Detecção | Resposta | Perda máxima |
|---|---|---|---|
| Queda do WebSocket | Coletor | Reconexão com espera exponencial (UC-02) | Segundos; lacuna registrada |
| Coletor trava ou morre | `systemd`; `Heartbeat` | Reinício automático; alarme | Segundos |
| Pipeline falha | Código de saída; `ReplicationLagMinutes` | Próxima execução retoma (idempotente) | Nenhuma (dados no disco) |
| Host travado | `StatusCheckFailed_Instance` | Reinicialização automática | Minutos |
| Falha de hardware | `StatusCheckFailed_System` | Recuperação automática do EC2 (mesmo disco) | Minutos |
| Perda do disco ou da instância | Alarmes | Recriar com Terraform; implantar | Até ~1 h de dados não replicados |
| Indisponibilidade da zona ou região | Alarmes | Aguardar; aceito pelo custo | Duração do evento |
| Disco cheio (replicação parada) | `disk_used_percent` | Alarme com ~30 dias de antecedência | Nenhuma, se tratado |
| Bloqueio dos endpoints `*.binance.vision` nos EUA | Teste de fumaça; `MaxChannelAgeSeconds`; `Heartbeat` | Plano B (DEC-26): plano pago, recursos avançados, SCP com Tóquio e `region` no Terraform | Até a troca |
| **Fechamento da conta** (fim do plano gratuito) | Calendário (CP-2) | Passar ao plano pago antes de 12/04/2027; cópia mensal offline dos dados brutos no MacBook ou em disco externo [PROPOSTA] | Todo o histórico, se ignorado |

---

## 12. Ambiente de desenvolvimento (MacBook)

- `uv` com Python 3.12; os mesmos testes da CI local.
- Execução local do coletor só para testes. Os dados gerados recebem `low_clock_confidence` quando o offset passar do limite (doc 06 §2.2). Recomenda-se ativar o ajuste automático de horário no macOS.
- Leitura dos dados de pesquisa: sincronização seletiva do S3 para um cache local, com credencial somente leitura temporária, num perfil dedicado da conta nova.
- Terraform executado do MacBook com o perfil da conta nova.

---

## 13. V0.2 e V0.3 — esboço

| Versão | Onde roda | Notas |
|---|---|---|
| V0.2 (pesquisa) | MacBook | DuckDB e Polars sobre o cache local de Parquet; treino de XGBoost local. Nenhum custo de nuvem adicional previsto. |
| V0.3 (paper trading) | Mesmo host de coleta | **ADR-11 (preliminar):** o Trading Engine abre a própria conexão de mercado, usando os mesmos módulos `sources` e `normalize` (PP-05), sem acoplar o seu ciclo de vida ao do coletor. A memória adicional (modelo e estado) será dimensionada no doc 03 v0.2, junto com o doc 05; pode exigir manter o t4g.small. |

---

## 14. Pendências

| Item | Quando |
|---|---|
| Confirmar a data de expiração dos créditos (id da conta já confirmado: 019419901344) | Antes do primeiro `terraform apply` |
| Conferir as dimensões das métricas StatsD publicadas pelo agente | Primeira implantação |
| Confirmar na fatura: franquia de IPv4 e teste do t4g.small no plano gratuito | CP-1 |
| Latência e offset a partir da us-east-2 (conectividade já medida no CloudShell) | Teste de fumaça (§9.4) |
| Termos de uso da Binance (doc 06 §12) | Antes da coleta contínua |
| Repositório Git do projeto | Antes da implementação |

---

## 15. Rastreabilidade

| Requisito | Elementos da arquitetura |
|---|---|
| RNF-001–RNF-005 (disponibilidade) | `systemd`, recuperação automática do EC2, rotação, troca sem lacuna (§4, §9.2, §11) |
| RNF-006–RNF-009 (desempenho e recursos) | Coletor enxuto, limites de memória, créditos `standard` (§4.1, §7.3) |
| RNF-010, RNF-011 (armazenamento) | Retenção local, S3, ciclo de vida (§6.3, §7.4) |
| RNF-012 (tempo) | chrony + Amazon Time Sync (§7.3) |
| RNF-013–RNF-017 (segurança) | §7.2, §8 |
| RNF-018 (logs) | Agente do CloudWatch, `/trader/*` (§7.5) |
| RNF-019–RNF-022 (manutenção e portabilidade) | Módulos e regra de dependência (§3, §5); `uv.lock`; ARM64 nos dois ambientes |
| RNF-023–RNF-025 (custo) | Tags, Budgets, camada gratuita (§7.5, §10) |
| RF-161–RF-166 (monitoramento) | Métricas, alarmes, SNS, `Heartbeat` (§7.5) |
| RF-181–RF-185 (operação) | CLI, arquivos de saúde, implantação (§4, §9) |

---

## 16. Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 0.1 | 23/10/2026 | Rascunho inicial: Data Engine e hospedagem AWS; DEC-21 a DEC-23; ADR-01 a ADR-12 propostas. |
| 0.2 | 23/10/2026 | ADR-01 a ADR-12 aprovadas (DEC-24); teto pós-créditos de R$ 100/mês; DEC-23 e CP-3 reescritos. |
| 0.3 | 23/10/2026 | §6.2 detalha o buffer e a sobreposição da rotação; §4.2 inclui as marcas d'água no `state.db` (ambos implementados no coletor). |
| 0.4 | 23/10/2026 | §5 inclui o módulo `pipeline`; §6.3 descreve órfãos, elegibilidade, renormalização por resumo das entradas, memória e réplica, como implementados. |
| 0.5 | 23/10/2026 | Conta 019419901344 confirmada; §7.5 com orçamento bruto e dimensões a verificar; §9 com o fluxo implementado (publish-artifact, trader-deploy, troca a/b, smoke); infraestrutura em `infra/`. |
| 0.6 | 23/10/2026 | DEC-26: região us-east-2 (a conta só aceita essa região; acesso à Binance medido); custos recalculados com preços da us-east-2; plano B para Tóquio. |
| 0.7 | 23/10/2026 | §9.2: a cor nova ignora as marcas d'água enquanto a antiga está ativa (lacunas falsas observadas na primeira troca em produção); §5: backfill no pacote `pipeline`. |
| 0.8 | 24/10/2026 | §9.2: correção manual das lacunas falsas da primeira troca; §5: `botocore[crt]` no MacBook. |
| 0.9 | 24/10/2026 | §5: relatório de qualidade no pacote `pipeline`. |
| 0.10 | 24/10/2026 | §4 e §6.4: recuperação de lacunas como etapa do pipeline horário, no lugar dos timers `trader-recovery` e `trader-archive`. |
