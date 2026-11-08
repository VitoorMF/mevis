# Documentação do MEVIS

Fase atual: **V0.1 (Data Engine) em operação, com G1 aprovado (DEC-30)**, coberta pelos docs 02, 03 e 06; **V0.2 (Pesquisa) em andamento**, pelo doc 04: etapa 1 (agregados por minuto) concluída; etapa 2 (features) aprovada (DEC-35) e implementada (comando `trader features`; doc 03 §5.1); etapa 3 (datasets e alvo) aprovada (DEC-37) e implementada (comando `trader dataset`; doc 03 §5.2). Os docs 04, 05, 07 e 08 vêm em paralelo. Nenhum código deve ser escrito antes de o documento correspondente estar aprovado.

| # | Documento | Status |
|---|---|---|
| 00 | [Contexto inicial](00-contexto-inicial.md) (transferência entre agentes) | Referência histórica, não normativa |
| 01 | [Product Requirements Document (PRD)](01-prd.md) | Rascunho v0.17 |
| 02 | [Software Requirements Specification (SRS)](02-srs.md) | Rascunho v0.11 — V0.1 detalhada; etapa 1 da V0.2 aprovada (DEC-32); etapa 2 (features, §4.11) aprovada (DEC-35); etapa 3 (datasets, §4.12) aprovada (DEC-37); resto da V0.2 e V0.3 delimitados |
| 03 | [System Architecture Document](03-architecture.md) | Rascunho v0.24 — Data Engine e AWS; features da V0.2 (§5.1, DEC-35), implementadas e medidas (MD-30); datasets (§5.2, DEC-37), implementados e medidos (MD-31); instância sob demanda (ADR-14, c7i-flex.large por DEC-33), primeira execução em 06/11/2026 (us-east-2); ADRs aprovadas; Terraform em `infra/` |
| 04 | [AI & Quantitative Research Specification](04-ai-research.md) | **Aprovado v1.5** — método da V0.2 (DEC-27, DEC-28); definições exatas das features (§5.3, DEC-34, DEC-35); alvo, pontos elegíveis e datasets (DEC-36, DEC-37) |
| 05 | Trading & Risk Engine Specification | Não iniciado |
| 06 | [Data & Integrations Specification](06-data-integrations.md) | Rascunho v0.22 — dados de mercado; agregados por minuto (§5.6), implementados e validados (MD-29), com preço de execução na versão 2 (DEC-37); regra de início de coleta na máscara (§8.1, §8.2) |
| 07 | Testing & Validation Plan | Não iniciado |
| 08 | Roadmap & Milestones | Não iniciado |

## Guias de apoio (não normativos)

| Guia | Para que serve |
|---|---|
| [Mapa do projeto](guias/mapa-do-projeto.md) | Visão de uma página: onde estamos, mapa do código e dos docs, o que é decisão sua |
| [Guia de ML para o MEVIS](guias/ml-para-o-projeto.md) | Mínimo de ML para julgar os resultados da V0.2: vocabulário, erros que invalidam tudo, o que estudar |

## Convenções

- Idioma: português (pt-BR). Nomes de componentes em inglês (Market Data Engine, Risk Manager, Execution Engine…).
- Etiquetas de afirmação: `[DECISÃO]`, `[PROPOSTA]`, `[HIPÓTESE]`, `[A VERIFICAR]`, `[PENDENTE]` — ver PRD §0.1.
- Identificadores rastreáveis: `OBJ`, `H`, `F`, `EX`, `PR`, `RS`, `IS`, `CA`, `R`, `DEC`, `PD` no PRD; `RF-xxx`, `RNF-xxx`, `RN-xxx` no SRS, sempre apontando para funcionalidades `F-xx` do PRD.
- Decisões pendentes ficam centralizadas no PRD §17.2 até serem resolvidas no documento indicado.
