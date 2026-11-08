# MEVIS — Market Evidence Intelligence System

Sistema de pesquisa e execução algorítmica para BTC/USDT na Binance. Fase atual: **V0.1 (Data Engine) em operação, G1 aprovado (DEC-30); V0.2 em andamento (etapa 1, agregados por minuto: concluída, DEC-32, DEC-33; etapa 2, features: implementada, DEC-34, DEC-35; etapa 3, datasets e alvo: implementada, DEC-36, DEC-37)** — índice e status em [docs/README.md](docs/README.md); escopo e decisões em [docs/01-prd.md](docs/01-prd.md); estrutura do código em docs/03-architecture.md §5.

Comandos: `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run trader collect --data-dir data/<nome> --duration <s>`, `uv run trader status --data-dir data/<nome>`, `uv run trader pipeline --data-dir data/<nome> [--replica-dir <dir>]`, `uv run trader backfill --data-dir data/<nome> --dataset trades|klines_1m --start AAAA-MM-DD --end AAAA-MM-DD`, `uv run trader quality --data-dir data/<nome> --start AAAA-MM-DD --end AAAA-MM-DD`, `uv run trader reconcile --config <toml> --start AAAA-MM-DD --end AAAA-MM-DD`, `uv run trader aggregates --data-dir data/<nome> [--replica-dir <dir>] --start AAAA-MM-DD --end AAAA-MM-DD`, `AWS_PROFILE=trader uv run trader features --config data/backfill-mac.toml --data-dir data/features-cache --start AAAA-MM-DD --end AAAA-MM-DD --out data/features/<nome>.parquet` (usa o grupo `pesquisa`, com numpy; recusa o holdout), `AWS_PROFILE=trader uv run trader dataset --config data/backfill-mac.toml --data-dir data/features-cache --track A|B --start AAAA-MM-DD --end AAAA-MM-DD` (grava em `data/datasets/<id>.parquet` e envia a `derived/datasets/<id>/`; exige `minute_trades` versão 2; recusa o holdout), `scripts/run-job.sh aggregates --start AAAA-MM-DD --end AAAA-MM-DD` (lança a EC2 sob demanda; mostra o custo e pede confirmação), `AWS_PROFILE=trader uv run trader dashboard-data --config data/backfill-mac.toml --data-dir data/dashboard-cache --out data/dashboard`, `uv run trader smoke --duration <s>`. Infraestrutura: `infra/README.md` (Terraform, perfil AWS `trader`).

## Regras de trabalho

- Documentação antes do código: não implementar nada sem o documento correspondente aprovado pelo usuário.
- Escrever em português (pt-BR); nomes de componentes em inglês.
- Não presumir lucratividade nem prometer retornos. Resultado negativo é resultado válido.
- Diferenciar fatos, hipóteses, propostas e pendências (etiquetas do PRD §0.1).
- Não fixar decisões em aberto (PRD §17.2) sem apresentar opções e trade-offs ao usuário.
- MVP: long/flat no spot, intraday em minutos, sem capital real, sem alavancagem, sem chaves de API de conta real.
- AWS: não criar recursos sem estimativa de custo aprovada pelo usuário (créditos de US$ 180; depois, teto de R$ 100/mês).
- Conta AWS: usar somente a conta nova 019419901344 (plano gratuito, com os créditos), perfil `trader`, região us-east-2 (a conta não aceita outras; Tóquio é o plano B, DEC-26). O perfil padrão do AWS CLI aponta para outra conta (724772086347) — nunca criar recursos nela. Ver docs/03-architecture.md §7.1.
- Risk Manager determinístico e independente do modelo; o modelo nunca acessa a execução diretamente.
- Features apenas com informação disponível no instante da decisão (point-in-time); mesmo código em pesquisa e operação.
- Evitar complexidade prematura (microservices, Kubernetes, filas distribuídas).

## Ambiente

MacBook Air M2, 16 GB RAM, 256 GB, macOS ARM64. Dependências precisam suportar ARM64; armazenamento é limitado.
