# Documentação do projeto

Fase atual: **engenharia de requisitos e planejamento**. Nenhum código de implementação deve ser escrito antes de o documento correspondente estar aprovado.

| # | Documento | Status |
|---|---|---|
| 00 | [Contexto inicial](00-contexto-inicial.md) (transferência entre agentes) | Referência histórica, não normativa |
| 01 | [Product Requirements Document (PRD)](01-prd.md) | Rascunho v0.6 |
| 02 | [Software Requirements Specification (SRS)](02-srs.md) | Rascunho v0.4 — V0.1 detalhada; V0.2 e V0.3 delimitadas |
| 03 | [System Architecture Document](03-architecture.md) | Rascunho v0.2 — Data Engine e AWS; ADRs aprovadas |
| 04 | AI & Quantitative Research Specification | Não iniciado |
| 05 | Trading & Risk Engine Specification | Não iniciado |
| 06 | [Data & Integrations Specification](06-data-integrations.md) | Rascunho v0.2 — dados de mercado da V0.1 |
| 07 | Testing & Validation Plan | Não iniciado |
| 08 | Roadmap & Milestones | Não iniciado |

## Convenções

- Idioma: português (pt-BR). Nomes de componentes em inglês (Market Data Engine, Risk Manager, Execution Engine…).
- Etiquetas de afirmação: `[DECISÃO]`, `[PROPOSTA]`, `[HIPÓTESE]`, `[A VERIFICAR]`, `[PENDENTE]` — ver PRD §0.1.
- Identificadores rastreáveis: `OBJ`, `H`, `F`, `EX`, `PR`, `RS`, `IS`, `CA`, `R`, `DEC`, `PD` no PRD; `RF-xxx`, `RNF-xxx`, `RN-xxx` no SRS, sempre apontando para funcionalidades `F-xx` do PRD.
- Decisões pendentes ficam centralizadas no PRD §17.2 até serem resolvidas no documento indicado.
