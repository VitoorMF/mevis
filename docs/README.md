# Documentação do MERIS

Fase atual: **V0.1 (Data Engine) em implementação**, coberta pelos docs 02, 03 e 06. Os docs 04, 05, 07 e 08 vêm em paralelo. Nenhum código deve ser escrito antes de o documento correspondente estar aprovado.

| # | Documento | Status |
|---|---|---|
| 00 | [Contexto inicial](00-contexto-inicial.md) (transferência entre agentes) | Referência histórica, não normativa |
| 01 | [Product Requirements Document (PRD)](01-prd.md) | Rascunho v0.9 |
| 02 | [Software Requirements Specification (SRS)](02-srs.md) | Rascunho v0.6 — V0.1 detalhada; V0.2 e V0.3 delimitadas |
| 03 | [System Architecture Document](03-architecture.md) | Rascunho v0.12 — Data Engine e AWS (us-east-2); ADRs aprovadas; Terraform em `infra/` |
| 04 | [AI & Quantitative Research Specification](04-ai-research.md) | Rascunho v0.1 — método da V0.2; PD-15 a PD-23 a decidir |
| 05 | Trading & Risk Engine Specification | Não iniciado |
| 06 | [Data & Integrations Specification](06-data-integrations.md) | Rascunho v0.12 — dados de mercado da V0.1 |
| 07 | Testing & Validation Plan | Não iniciado |
| 08 | Roadmap & Milestones | Não iniciado |

## Guias de apoio (não normativos)

| Guia | Para que serve |
|---|---|
| [Mapa do projeto](guias/mapa-do-projeto.md) | Visão de uma página: onde estamos, mapa do código e dos docs, o que é decisão sua |
| [Guia de ML para o MERIS](guias/ml-para-o-projeto.md) | Mínimo de ML para julgar os resultados da V0.2: vocabulário, erros que invalidam tudo, o que estudar |

## Convenções

- Idioma: português (pt-BR). Nomes de componentes em inglês (Market Data Engine, Risk Manager, Execution Engine…).
- Etiquetas de afirmação: `[DECISÃO]`, `[PROPOSTA]`, `[HIPÓTESE]`, `[A VERIFICAR]`, `[PENDENTE]` — ver PRD §0.1.
- Identificadores rastreáveis: `OBJ`, `H`, `F`, `EX`, `PR`, `RS`, `IS`, `CA`, `R`, `DEC`, `PD` no PRD; `RF-xxx`, `RNF-xxx`, `RN-xxx` no SRS, sempre apontando para funcionalidades `F-xx` do PRD.
- Decisões pendentes ficam centralizadas no PRD §17.2 até serem resolvidas no documento indicado.
