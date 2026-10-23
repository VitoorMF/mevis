# Mapa do projeto MERIS

> **Não normativo.** Resumo de orientação, escrito em 23/10/2026. Em caso de divergência, valem o PRD e os demais documentos numerados. Se algo aqui estiver desatualizado, os documentos mandam.

**MERIS** (*Market Evidence & Research Intelligence System*, DEC-25) é um sistema de pesquisa e execução algorítmica para **BTC/USDT na Binance**. Antes de ser um robô, é um **instrumento de pesquisa**: a pergunta central é se combinar candles, volume, fluxo de negócios e livro de ofertas gera lucro líquido de custos, fora da amostra e melhor que alternativas simples. A resposta "não" é um resultado válido.

---

## 1. Onde estamos

| Versão | O que é | Situação |
|---|---|---|
| **V0.1 — Data Engine** | Coleta, valida e guarda os dados de mercado | **Em implementação.** Coletor, pipeline horário e infraestrutura AWS em código (`infra/`); o Git já tem commits para cada um |
| **V0.2 — Pesquisa** | Features, backtester, treino de XGBoost, ablações B0–B7 | Não iniciada. Depende do doc 04 (não escrito) |
| **V0.3 — Paper trading** | Opera em simulação, ao vivo, com Risk Manager | Não iniciada. Depende do doc 05 (não escrito) |
| **V0.4 — Macro** | Notícias e macro como filtro de risco | Pós-MVP |

Limites do MVP: spot, long/flat, intraday em minutos, **sem capital real, sem alavancagem, sem chaves de conta real**.

## 2. Como o dado percorre o sistema (V0.1)

```text
Binance (WebSocket + REST)
        │
   sources/  ──►  ingest/  ──►  bruto em disco (JSONL + zstd)
                                     │
                          pipeline/ (a cada hora)
                                     │
        normalize/ ──► quality/ ──► Parquet normalizado + máscara de qualidade
                                     │
                              réplica no S3
                                     │
                     (V0.2) cache local no MacBook → DuckDB / Polars → XGBoost
```

Em português: a **coleta** roda 24/7 numa máquina da AWS, **guarda tudo bruto**, e uma rotina por hora **limpa, valida e converte** os dados para um formato de análise. A pesquisa vem depois, no seu MacBook.

## 3. Mapa do código (`src/trader/`)

> O nome do pacote ainda é `trader`; o renome para `meris` está planejado como commit separado (DEC-25).

| Pasta | O que faz |
|---|---|
| `sources/` | Conversa com a Binance (WebSocket e REST, com limitador de peso) |
| `ingest/` | Coletor contínuo, gravação do bruto, rotação de conexão, medição de relógio |
| `normalize/` | Transforma o formato da Binance no formato interno (mesmo código ao vivo e em lote) |
| `quality/` | Regras de validação e detecção de lacunas |
| `recovery/` | Recuperação de lacunas |
| `pipeline/` | Rodada horária: compressão, normalização, máscara de qualidade, réplica, retenção |
| `storage/` | Layout de arquivos, Parquet, S3 |
| `core/` | Modelos de dados, tempo e decimais (sem E/S) |
| `ops/` | Configuração, logs, métricas, saúde, estado |
| `cli/` | Comandos: `collect`, `status`, `pipeline`, `smoke` |

Fora do pacote: `tests/` (testes automatizados), `infra/` (Terraform da AWS), `scripts/`, `docs/`.

## 4. Mapa dos documentos (`docs/`)

| Doc | Para que serve | Estado |
|---|---|---|
| 01 PRD | O quê e por quê: escopo, hipóteses, decisões | Rascunho v0.6 |
| 02 SRS | Requisitos detalhados da V0.1 | Rascunho v0.4 |
| 03 Arquitetura | Como o sistema é montado e roda na AWS | Rascunho v0.3 |
| 06 Dados e integrações | Formato e comportamento dos dados da Binance | Rascunho v0.3 |
| 04 IA e pesquisa | Modelos, features, validação | **Não iniciado** |
| 05 Trading e risco | Risk Manager, execução simulada | **Não iniciado** |
| 07 Testes e validação | Critérios de aceitação e plano de testes | **Não iniciado** |
| 08 Roadmap | Marcos e prazos | **Não iniciado** |

Para estudar ML: [ml-para-o-projeto.md](ml-para-o-projeto.md).

## 5. O que é decisão sua e o que pode ser delegado

**É sua** (o projeto exige aprovação sua):

- Aprovar cada documento antes de qualquer código.
- Criar recursos na AWS e gastar créditos (só com estimativa de custo aprovada).
- Escolher entre opções de decisões em aberto (§6).
- Aceitar ou rejeitar a conclusão de cada hipótese.
- Qualquer coisa que envolva dinheiro real (hoje fora do escopo).

**Pode ser delegado ao Claude** (com revisão):

- Escrever e testar código a partir de documentos aprovados.
- Rodar testes, lint e checagem de tipos.
- Redigir rascunhos de documentos, sempre para você aprovar.

**Nunca delegar sem entender**: resultados de modelos (ver o guia de ML) e qualquer conclusão sobre lucratividade.

## 6. Decisões em aberto (PRD §17.2)

| ID | Questão | Recomendação atual |
|---|---|---|
| PD-07 | Critérios quantitativos de G2 e G3 | Pré-registrar nos docs 04 e 07, **antes** de avaliar o holdout |
| PD-08 | Paper trading pela testnet ou por simulador local | Simulador local; testnet só para validar integração |
| PD-09 | A V0.3 depende da V0.2? | O motor não depende; a escolha do modelo sim (G2) |
| PD-10 | Interface de operação | CLI, relatórios e logs |
| PD-11 | Spot × futuros no longo prazo | Adiar para depois do MVP |

## 7. Como verificar por conta própria

Na raiz do projeto:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
git log --oneline
```

- `pytest` roda os testes automatizados; tudo deve passar.
- `ruff` e `mypy` checam estilo e tipos.
- O `git log` mostra o que foi feito e quando.

## 8. Sinais de alerta

- Um documento marcado como aprovado que você não leu.
- Código novo sem documento correspondente.
- Resultado de modelo bom demais, sem baselines ao lado.
- Qualquer recurso AWS criado sem estimativa de custo.
- Qualquer chave de API de conta real em arquivo ou configuração.

## 9. Próximos passos naturais

1. Fechar a V0.1: smoke test na AWS, coleta contínua e relatório de qualidade (G1).
2. Escrever o **doc 04** (IA e pesquisa), de preferência depois de ler o guia de ML.
3. Escrever o **doc 07** (testes e validação), incluindo os critérios de G2.
4. Renomear o pacote `trader` para `meris`, em commit separado.
