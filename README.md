# MERIS

**MERIS — Market Evidence & Research Intelligence System**: sistema de trading algorítmico com IA. Fase atual: **V0.1 — Data Engine**. O pacote Python se chama `trader`.

- Documentação: [docs/README.md](docs/README.md)
- Arquitetura do código: [docs/03-architecture.md](docs/03-architecture.md) §5

## Desenvolvimento

Requer [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```
