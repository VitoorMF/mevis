"""Linha de comando: `collect`, `collector-ready`, `status`, `pipeline`, `reconcile`, `backfill`,
`aggregates`, `features`, `dataset`, `baselines`, `experiments`, `walkforward`, `report`, `quality`,
`dashboard-data`, `status-publisher` e `smoke` (RF-181 a RF-187, RF-213, RF-229, RF-247, RF-257,
RF-276, RF-282)."""

import argparse
import asyncio
import hashlib
import json
import re
import signal
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

from trader.core.holdout import HoldoutRefused
from trader.ingest.collector import Collector
from trader.ingest.raw_writer import RawWriter
from trader.ops.clock import SystemClock
from trader.ops.config import (
    CollectorConfig,
    ConfigError,
    PipelineConfig,
    load_config,
    load_pipeline_config,
)
from trader.ops.health import health_files, health_path, is_ready, read_health
from trader.ops.logs import configure_logging
from trader.ops.metrics import Metrics, NullMetrics, StatsdMetrics
from trader.ops.smoke import run_smoke
from trader.ops.state import StateStore
from trader.pipeline.aggregates import AggregatesJob, JobOptions, job_store
from trader.pipeline.backfill import Backfill, BackfillBusy, BackfillReport
from trader.pipeline.dashboard_data import export, sync_from_s3
from trader.pipeline.hourly import HourlyPipeline, PipelineBusy
from trader.pipeline.public_status import DirectorySink, S3Sink, Sink, StatusPublisher
from trader.pipeline.quality_report import build_report
from trader.recovery.gaps import GapRecovery
from trader.sources.binance_archive import DATASETS, ArchiveClient, ArchiveError
from trader.sources.binance_rest import BinanceRest, WeightLimiter
from trader.sources.binance_ws import websockets_connector
from trader.storage.layout import HOUR_US, StorageError
from trader.storage.objectstore import LocalDirectoryStore, ObjectStore, S3ObjectStore

STALE_HEALTH_S = 30
_STALE_US = STALE_HEALTH_S * 1_000_000
PIPELINE_COMMANDS = (
    "pipeline",
    "reconcile",
    "backfill",
    "aggregates",
    "features",
    "dataset",
    "baselines",
    "experiments",
    "walkforward",
    "report",
    "quality",
    "dashboard-data",
    "status-publisher",
)
"""Comandos configurados pela tabela `[pipeline]`."""
REGISTRY = Path("data/experiments/registry.sqlite")
"""Registro de experimentos no MacBook, fora do Git (doc 03 §5.3)."""
REPORTS = Path("data/reports")
"""Relatórios de desenvolvimento no MacBook, fora do Git (doc 03 §5.4)."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trader")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="executa o coletor contínuo")
    collect.add_argument("--config", type=Path, help="arquivo TOML com a tabela [collector]")
    collect.add_argument("--data-dir", type=Path, help="diretório de dados (padrão: ./data)")
    collect.add_argument("--instance", help="nome da instância (troca sem lacuna: a, b)")
    collect.add_argument("--duration", type=float, help="para após N segundos (testes locais)")
    collect.add_argument("--log-level", default="INFO")

    status = commands.add_parser("status", help="mostra a saúde do coletor")
    status.add_argument("--config", type=Path)
    status.add_argument("--data-dir", type=Path)

    pipeline = commands.add_parser("pipeline", help="executa uma rodada do pipeline horário")
    pipeline.add_argument("--config", type=Path, help="arquivo TOML com a tabela [pipeline]")
    pipeline.add_argument("--data-dir", type=Path)
    pipeline.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    pipeline.add_argument(
        "--rebuild", action="store_true", help="normaliza de novo todas as horas (RF-133)"
    )
    pipeline.add_argument("--log-level", default="INFO")

    backfill = commands.add_parser(
        "backfill", help="importa histórico dos arquivos públicos da Binance (doc 06 §7)"
    )
    backfill.add_argument("--config", type=Path, help="arquivo TOML com a tabela [pipeline]")
    backfill.add_argument("--data-dir", type=Path)
    backfill.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    backfill.add_argument("--dataset", required=True, choices=DATASETS)
    backfill.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    backfill.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    backfill.add_argument("--log-level", default="INFO")

    aggregates = commands.add_parser(
        "aggregates", help="agregados por minuto de um período (doc 06 §5.6; doc 03 §7.8)"
    )
    aggregates.add_argument("--config", type=Path, help="arquivo TOML com a tabela [pipeline]")
    aggregates.add_argument("--data-dir", type=Path, help="diretório de trabalho")
    aggregates.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    aggregates.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    aggregates.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    aggregates.add_argument(
        "--allow-holdout",
        action="store_true",
        help="só na avaliação final: permite o holdout da trilha A (RF-179)",
    )
    aggregates.add_argument("--workers", type=int, default=1, help="processos em paralelo")
    aggregates.add_argument("--job-id", help="identificador do manifesto (padrão: data e hora)")
    aggregates.add_argument("--code-version", help="versão registrada no manifesto")
    aggregates.add_argument("--log-level", default="INFO")

    features = commands.add_parser(
        "features", help="features point-in-time de um período num Parquet local (RF-213)"
    )
    features.add_argument("--config", type=Path, help="TOML com replica_s3_bucket para ler do S3")
    features.add_argument(
        "--data-dir", type=Path, help="cache local (com S3) ou diretório de dados"
    )
    features.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    features.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    features.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    features.add_argument("--out", type=Path, required=True, help="arquivo Parquet de saída")
    features.add_argument(
        "--allow-holdout",
        action="store_true",
        help="só na avaliação final: permite o holdout da trilha A (RF-204)",
    )

    dataset = commands.add_parser(
        "dataset", help="dataset de uma trilha e período: features e alvo (RF-229)"
    )
    dataset.add_argument("--config", type=Path, help="TOML com replica_s3_bucket para ler do S3")
    dataset.add_argument("--data-dir", type=Path, help="cache local (com S3) ou diretório de dados")
    dataset.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    dataset.add_argument("--track", required=True, choices=("A", "B"), help="trilha (doc 04 §3.2)")
    dataset.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    dataset.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    dataset.add_argument(
        "--out-dir", type=Path, default=Path("data/datasets"), help="pasta local do Parquet"
    )
    dataset.add_argument(
        "--allow-holdout",
        action="store_true",
        help="só na avaliação final: permite o holdout e grava o registro de acesso (RF-228)",
    )

    baselines = commands.add_parser(
        "baselines", help="baselines B0 a B3 de um dataset, em todos os cenários (RF-257)"
    )
    baselines.add_argument("--config", type=Path, help="TOML com replica_s3_bucket (S3)")
    baselines.add_argument("--data-dir", type=Path, help="diretório de dados (sem S3)")
    baselines.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    baselines.add_argument("--dataset", required=True, help="id do dataset (RF-227)")
    baselines.add_argument("--horizon", required=True, type=int, help="H em minutos (15 ou 60)")
    baselines.add_argument("--start", type=date.fromisoformat, help="1º dia da janela")
    baselines.add_argument("--end", type=date.fromisoformat, help="último dia da janela")
    baselines.add_argument("--b2-trades", type=int, help="número de operações sorteadas no B2")
    baselines.add_argument("--seed", type=int, default=0, help="semente do B2")
    baselines.add_argument(
        "--datasets-dir", type=Path, default=Path("data/datasets"), help="pasta dos datasets"
    )
    baselines.add_argument("--registry", type=Path, default=REGISTRY, help="SQLite do registro")
    baselines.add_argument(
        "--allow-holdout",
        action="store_true",
        help="só na avaliação final: aceita um dataset de avaliação final (RF-256)",
    )

    experiments = commands.add_parser("experiments", help="registro de experimentos (RF-276)")
    experiments.add_argument("action", choices=("list", "show", "count", "discard", "rebuild"))
    experiments.add_argument("execution_id", nargs="?", help="id da execução (show, discard)")
    experiments.add_argument("--track", choices=("A", "B"), help="filtra por trilha")
    experiments.add_argument("--reason", help="motivo do descarte")
    experiments.add_argument("--config", type=Path, help="TOML com replica_s3_bucket (S3)")
    experiments.add_argument("--data-dir", type=Path, help="diretório de dados (sem S3)")
    experiments.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    experiments.add_argument("--registry", type=Path, default=REGISTRY, help="SQLite do registro")

    walkforward = commands.add_parser(
        "walkforward", help="walk-forward de B4 a B6 num dataset de desenvolvimento (RF-247)"
    )
    walkforward.add_argument("--config", type=Path, help="TOML com replica_s3_bucket (S3)")
    walkforward.add_argument("--data-dir", type=Path, help="diretório de dados (sem S3)")
    walkforward.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    walkforward.add_argument("--dataset", required=True, help="id do dataset (RF-227)")
    walkforward.add_argument("--rungs", type=_names, default=("B4", "B5", "B6"), help="B4,B5,B6")
    walkforward.add_argument("--horizons", type=_ints, default=(15, 60), help="15,60")
    walkforward.add_argument(
        "--models", type=_names, default=("logistic", "xgboost"), help="logistic,xgboost"
    )
    walkforward.add_argument("--seed", type=int, default=0, help="semente dos modelos")
    walkforward.add_argument(
        "--datasets-dir", type=Path, default=Path("data/datasets"), help="cache dos datasets"
    )
    walkforward.add_argument("--registry", type=Path, default=REGISTRY, help="SQLite do registro")

    report = commands.add_parser(
        "report", help="relatório de desenvolvimento a partir do registro (RF-282)"
    )
    report.add_argument("--config", type=Path, help="TOML com replica_s3_bucket (S3)")
    report.add_argument("--data-dir", type=Path, help="diretório de dados (sem S3)")
    report.add_argument("--replica-dir", type=Path, help="réplica local (desenvolvimento)")
    report.add_argument("--dataset", required=True, help="id do dataset (RF-227)")
    report.add_argument("--seed", type=int, default=0, help="semente do bootstrap e de B2")
    report.add_argument(
        "--datasets-dir", type=Path, default=Path("data/datasets"), help="cache dos datasets"
    )
    report.add_argument("--registry", type=Path, default=REGISTRY, help="SQLite do registro")
    report.add_argument("--reports-dir", type=Path, default=REPORTS, help="onde gravar o Markdown")

    quality = commands.add_parser(
        "quality", help="relatório de qualidade de um período (doc 06 §8.3)"
    )
    quality.add_argument("--config", type=Path, help="arquivo TOML com a tabela [pipeline]")
    quality.add_argument("--data-dir", type=Path)
    quality.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    quality.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    quality.add_argument("--json", type=Path, help="grava também o relatório em JSON")

    reconcile = commands.add_parser(
        "reconcile", help="confere um período que já saiu do disco (doc 06 §9.2)"
    )
    reconcile.add_argument("--config", type=Path, help="arquivo TOML com a tabela [pipeline]")
    reconcile.add_argument("--data-dir", type=Path)
    reconcile.add_argument("--start", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    reconcile.add_argument("--end", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    reconcile.add_argument("--log-level", default="INFO")

    ready = commands.add_parser(
        "collector-ready", help="a instância iniciada nesta troca está recebendo? (RF-186)"
    )
    ready.add_argument("--config", type=Path)
    ready.add_argument("--data-dir", type=Path)
    ready.add_argument("--instance", required=True)
    ready.add_argument("--since-us", required=True, type=int, help="início da troca, em µs")

    dashboard = commands.add_parser(
        "dashboard-data", help="gera as tabelas do painel da coleta (doc 06 §8.4)"
    )
    dashboard.add_argument(
        "--config", type=Path, help="TOML com replica_s3_bucket para baixar do S3"
    )
    dashboard.add_argument("--data-dir", type=Path, help="cache local (com S3) ou dados do host")
    dashboard.add_argument("--out", type=Path, required=True, help="diretório dos CSVs")

    publisher = commands.add_parser(
        "status-publisher", help="publica a página pública de status (doc 03 §7.7)"
    )
    publisher.add_argument("--config", type=Path, help="TOML com public_bucket em [pipeline]")
    publisher.add_argument("--data-dir", type=Path)
    publisher.add_argument("--out", type=Path, help="grava num diretório local em vez do bucket")
    publisher.add_argument("--once", action="store_true", help="uma rodada só")
    publisher.add_argument("--log-level", default="INFO")

    smoke = commands.add_parser("smoke", help="testa a conectividade com a Binance (doc 03 §9.4)")
    smoke.add_argument("--config", type=Path)
    smoke.add_argument("--duration", type=float, default=600.0, help="segundos de WebSocket")

    args = parser.parse_args(argv)
    try:
        if args.command in PIPELINE_COMMANDS:
            pipeline_config = load_pipeline_config(
                args.config,
                data_dir=args.data_dir,
                replica_dir=getattr(args, "replica_dir", None),
            )
        else:
            config = load_config(
                args.config,
                data_dir=getattr(args, "data_dir", None),
                instance=getattr(args, "instance", None),
            )
    except ConfigError as exc:
        print(f"configuração inválida: {exc}", file=sys.stderr)
        return 2
    if args.command == "pipeline":
        configure_logging(args.log_level)
        return run_pipeline(pipeline_config, rebuild=args.rebuild)
    if args.command == "reconcile":
        configure_logging(args.log_level)
        return run_reconcile(pipeline_config, args.start, args.end)
    if args.command == "collector-ready":
        health = read_health(health_path(config.data_dir, args.instance))
        return 0 if is_ready(health, args.since_us, time.time_ns() // 1000, _STALE_US) else 1
    if args.command == "backfill":
        configure_logging(args.log_level)
        return run_backfill(pipeline_config, args.dataset, args.start, args.end)
    if args.command == "aggregates":
        configure_logging(args.log_level)
        return run_aggregates(pipeline_config, args)
    if args.command == "features":
        return run_features(pipeline_config, args)
    if args.command == "dataset":
        return run_dataset(pipeline_config, args)
    if args.command == "baselines":
        return run_baselines(pipeline_config, args)
    if args.command == "experiments":
        return run_experiments(pipeline_config, args)
    if args.command == "walkforward":
        return run_walkforward(pipeline_config, args)
    if args.command == "report":
        return run_report(pipeline_config, args)
    if args.command == "quality":
        return quality_report(pipeline_config, args.start, args.end, args.json)
    if args.command == "dashboard-data":
        return dashboard_data(pipeline_config, args.out)
    if args.command == "status-publisher":
        configure_logging(args.log_level)
        return publish_status(pipeline_config, args.out, once=args.once)
    if args.command == "collect":
        configure_logging(args.log_level)
        asyncio.run(run_collector(config, args.duration))
        return 0
    if args.command == "smoke":
        return asyncio.run(smoke_test(config, args.duration))
    return show_status(config)


async def run_collector(config: CollectorConfig, duration_s: float | None = None) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    if duration_s is not None:
        loop.call_later(duration_s, stop.set)

    clock = SystemClock()
    state = StateStore(config.data_dir / "state.db")
    metrics = _metrics(config.statsd_host, config.statsd_port)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            rest = BinanceRest(
                config.rest_base_url,
                WeightLimiter(config.rest_weight_budget_per_min, clock),
                clock,
                client,
            )
            collector = Collector(
                config,
                clock=clock,
                writer=RawWriter(config.data_dir, config.symbol),
                state=state,
                rest=rest,
                metrics=metrics,
                connector=websockets_connector,
                code_version=version("trader"),
            )
            await collector.run(stop)
    finally:
        metrics.close()
        state.close()


def show_status(config: CollectorConfig) -> int:
    paths = health_files(config.data_dir)
    if not paths:
        print(f"sem arquivo de saúde em {config.data_dir / 'health'}")
        return 1
    now_us = time.time_ns() // 1000
    healthy = False
    for path in paths:
        health = read_health(path)
        if health is None:
            print(f"{path.name}: ilegível")
            continue
        healthy |= _print_health(health, now_us)
    _print_recent_gaps(config.data_dir / "state.db", now_us)
    return 0 if healthy else 1


def _print_health(health: dict[str, Any], now_us: int) -> bool:
    age_s = (now_us - int(health["updated_ts_us"])) / 1e6
    instance = health.get("instance", "main")
    print(
        f"coletor [{instance}]: {health['status']} (atualizado há {age_s:.0f} s)"
        f" — versão {health['version']}"
    )
    print(f"sessão primária: {health['primary_session']} ({health['primary_endpoint']})")
    print(f"{'canal':<26}{'mensagens':>12}{'última':>12}")
    for stream, info in health["channels"].items():
        last = info["last_recv_ts_us"]
        last_text = f"{(now_us - last) / 1e6:.1f} s" if last else "—"
        print(f"{stream:<26}{info['messages']:>12}{last_text:>12}")
    offset = health["clock_offset_us"]
    offset_text = f"{offset / 1000:+.0f} ms" if offset is not None else "—"
    print(
        f"lacunas: {health['gaps_detected']} · reconexões: {health['reconnects']} · "
        f"mensagens inválidas: {health['malformed_messages']} · offset de relógio: {offset_text}"
    )
    return bool(health["status"] == "running" and age_s <= STALE_HEALTH_S)


def _print_recent_gaps(state_path: Path, now_us: int) -> None:
    if not state_path.exists():
        return
    with StateStore(state_path) as state:
        recent = state.gaps_since(now_us - 24 * 3600 * 1_000_000)
    if not recent:
        return
    print("lacunas nas últimas 24 h:")
    for gap in recent[-10:]:
        record = gap.record
        ids = (
            f" ids {record.first_missing_id}-{record.last_missing_id}"
            if record.first_missing_id is not None
            else ""
        )
        duration = (record.end_ts_us - record.start_ts_us) / 1e6
        print(f"  {record.channel} {record.cause} {duration:.1f} s{ids} [{gap.status}]")


def run_pipeline(config: PipelineConfig, *, rebuild: bool = False) -> int:
    store = _object_store(config)
    with (
        StateStore(config.data_dir / "state.db") as state,
        httpx.Client(timeout=60.0) as client,
    ):
        recovery = None
        if config.recovery_enabled:
            archive = ArchiveClient(client, config.archive_base_url)
            recovery = GapRecovery(config, state, client, archive)
        try:
            report = HourlyPipeline(config, state, store, recovery).run(
                time.time_ns() // 1000, rebuild=rebuild
            )
        except PipelineBusy as exc:
            print(str(exc), file=sys.stderr)
            return 3
    if report.replication_lag_minutes is not None:
        metrics = _metrics(config.statsd_host, config.statsd_port)
        metrics.gauge("ReplicationLagMinutes", report.replication_lag_minutes)
        metrics.close()
    if report.continuity_gaps:
        print(f"buracos entre sessões registrados: {report.continuity_gaps}")
    if report.aggregate_failures:
        print(f"agregados por minuto falharam em {report.aggregate_failures} hora(s); ver o log")
    hours = ", ".join(_hour_label(h) for h in report.normalized_hours) or "nenhuma"
    print(f"órfãos fechados: {report.sealed_orphans} · comprimidos: {report.compressed}")
    print(
        f"lacunas: recuperadas {report.recovered_gaps} · irrecuperáveis"
        f" {report.irrecoverable_gaps} · pendentes {report.pending_gaps}"
    )
    print(f"horas normalizadas: {hours}")
    if report.normalized_hours:
        rows = " · ".join(f"{k}: {v}" for k, v in sorted(report.rows.items()))
        duplicates = sum(report.duplicates.values())
        print(f"linhas: {rows} · duplicatas descartadas: {duplicates}")
        print(f"quarentena: {report.quarantined}")
    replica = "desligada" if store is None else f"{report.replicated} arquivo(s)"
    print(f"réplica: {replica} · removidos pela retenção: {report.deleted}")
    return 0


def run_backfill(config: PipelineConfig, dataset: str, start: date, end: date) -> int:
    if start > end:
        print(f"início {start} depois do fim {end}", file=sys.stderr)
        return 2
    store = _object_store(config)
    with (
        StateStore(config.data_dir / "state.db") as state,
        httpx.Client(timeout=60.0) as client,
    ):
        try:
            report = Backfill(config, state, store, ArchiveClient(client)).run(dataset, start, end)
        except BackfillBusy as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except ArchiveError as exc:
            print(
                f"interrompido: {exc}. O que já foi importado fica; rode de novo.", file=sys.stderr
            )
            return 1
    _print_backfill(report, replicated=store is not None)
    return 1 if report.with_status("rejected") else 0


def run_reconcile(config: PipelineConfig, start: date, end: date) -> int:
    if start > end:
        print(f"início {start} depois do fim {end}", file=sys.stderr)
        return 2
    store = _object_store(config)
    if store is None:
        print("reconcile precisa de réplica configurada", file=sys.stderr)
        return 2
    start_hour = (start - date(1970, 1, 1)).days * 24
    end_hour = ((end - date(1970, 1, 1)).days + 1) * 24
    with (
        StateStore(config.data_dir / "state.db") as state,
        httpx.Client(timeout=60.0) as client,
    ):
        archive = ArchiveClient(client, config.archive_base_url)
        recovery = GapRecovery(config, state, client, archive)
        try:
            report = HourlyPipeline(config, state, store, recovery).reconcile(
                start_hour, end_hour, time.time_ns() // 1000
            )
        except PipelineBusy as exc:
            print(str(exc), file=sys.stderr)
            return 3
    print(f"arquivos restaurados: {report.restored}")
    print(f"buracos registrados: {report.continuity_gaps}")
    print(
        f"lacunas: recuperadas {report.recovered_gaps} · irrecuperáveis"
        f" {report.irrecoverable_gaps} · pendentes {report.pending_gaps}"
    )
    print(f"horas normalizadas: {len(report.normalized_hours)}")
    return 0


def run_aggregates(config: PipelineConfig, args: argparse.Namespace) -> int:
    job_id = args.job_id or time.strftime("aggregates-%Y%m%dT%H%M%SZ", time.gmtime())
    if not re.fullmatch(r"[A-Za-z0-9._-]+", job_id) or args.workers < 1:
        print(
            "--job-id só com letras, números, ponto, hífen e sublinhado; --workers ≥ 1",
            file=sys.stderr,
        )
        return 2
    options = JobOptions(
        start=args.start,
        end=args.end,
        job_id=job_id,
        code_version=args.code_version or version("trader"),
        allow_holdout=args.allow_holdout,
        workers=args.workers,
    )
    try:
        report = AggregatesJob(config, job_store(config), options).run()
    except HoldoutRefused as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2
    except StorageError as exc:
        print(f"falhou: {exc}. O que foi gravado está completo; rode de novo.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    manifest = report.manifest
    units = manifest["units"]
    print(
        f"dias de arquivo: {sum(u['kind'] == 'archive' for u in units)}"
        f" · horas ao vivo: {sum(u['kind'] == 'live' for u in units)}"
        f" · negócios: {manifest['trades']}"
    )
    print(
        f"saídas: {len(manifest['outputs'])} · duração: {manifest['duration_s']} s"
        f" · pico de memória por processo: {manifest['max_rss_mb']} MB"
    )
    for warning in report.warnings:
        print(f"aviso: {warning}")
    print(f"manifesto: {report.manifest_key}")
    return 0


def run_features(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Monta a série, calcula as features e grava o Parquet (doc 03 §5.1). O `numpy` só é
    importado aqui: o host não o tem até a V0.3 (RNF-029)."""
    import resource

    try:
        from trader.features.compute import catalog, catalog_id, compute_features
        from trader.features.series import series_from_table
        from trader.pipeline.minutes import CachedS3Source, LocalSource, Source, assemble_minutes
        from trader.storage.s3cache import S3Cache
    except ImportError as exc:
        print(f"falta o grupo de pesquisa (numpy): {exc}", file=sys.stderr)
        return 2
    import pyarrow.parquet as pq

    began = time.monotonic()
    source: Source
    cache = None
    if config.replica_s3_bucket:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        cache = S3Cache(client, config.replica_s3_bucket, config.data_dir)
        source = CachedS3Source(cache)
    else:
        source = LocalSource(config.replica_dir or config.data_dir)
    try:
        minutes = assemble_minutes(
            source,
            symbol=config.symbol,
            kline_channel=config.streams[1],
            start=args.start,
            end=args.end,
            allow_holdout=args.allow_holdout,
        )
    except HoldoutRefused as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if cache is not None:
            cache.save()
    assembled = time.monotonic()
    series = series_from_table(minutes.table)
    del minutes.table
    frame = compute_features(series)
    del series
    computed = time.monotonic()
    table = frame.to_table()
    inputs = sorted(minutes.inputs, key=lambda i: i["key"])
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    metadata = {
        "catalog_id": catalog_id(),
        "catalog": catalog(),
        "code_version": version("trader"),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "allow_holdout": minutes.allow_holdout,
        "holdout_days": minutes.holdout_days,
        "inputs": len(inputs),
        "inputs_sha256": digest,
    }
    table = table.replace_schema_metadata({"mevis.features": json.dumps(metadata)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.out, compression="zstd")
    rows = table.num_rows
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1 << 20 if sys.platform == "darwin" else 1 << 10
    )
    if cache is not None:
        print(f"S3: {cache.downloaded} arquivo(s) baixado(s), {cache.unchanged} sem mudança")
    print(
        f"minutos: {rows} · dias de arquivo: {minutes.archive_days}"
        f" · horas ao vivo: {minutes.live_hours}"
    )
    for family, valid in frame.valid.items():
        print(f"  pontos válidos em {family}: {int(valid.sum())} ({valid.mean():.1%})")
    print(
        f"montagem: {assembled - began:.1f} s · cálculo: {computed - assembled:.1f} s"
        f" · pico de memória: {peak_mb:.0f} MB"
    )
    for warning in minutes.warnings:
        print(f"aviso: {warning}")
    if minutes.holdout_days:
        print(f"inclui {minutes.holdout_days} dia(s) do holdout (avaliação final)")
    print(f"catálogo {metadata['catalog_id']} → {args.out}")
    return 0


def run_dataset(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Constrói o dataset de uma trilha e período (doc 03 §5.2; RF-229). O `numpy` só é importado
    aqui, como em `features` (RNF-029)."""
    import getpass
    import resource
    import socket

    try:
        from trader.datasets.labels import LabelParams
        from trader.pipeline.datasets import DatasetRequest, build_dataset, code_version
        from trader.pipeline.minutes import CachedS3Source, LocalSource, Source
        from trader.storage.objectstore import LocalDirectoryStore, ObjectStore, S3ObjectStore
        from trader.storage.s3cache import S3Cache
    except ImportError as exc:
        print(f"falta o grupo de pesquisa (numpy): {exc}", file=sys.stderr)
        return 2

    began = time.monotonic()
    source: Source
    store: ObjectStore
    cache = None
    if config.replica_s3_bucket:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        cache = S3Cache(client, config.replica_s3_bucket, config.data_dir)
        source = CachedS3Source(cache)
        store = S3ObjectStore(client, config.replica_s3_bucket)
    else:
        root = config.replica_dir or config.data_dir
        source, store = LocalSource(root), LocalDirectoryStore(root)
    code = code_version()
    if code.endswith(".dirty"):
        print(f"aviso: código com mudanças sem commit ({code})", file=sys.stderr)
    request = DatasetRequest(args.track, args.start, args.end, args.allow_holdout, LabelParams())
    try:
        built = build_dataset(
            source,
            symbol=config.symbol,
            kline_channel=config.streams[1],
            request=request,
            code=code,
            out_dir=args.out_dir,
            store=store,
            now=datetime.now(UTC),
            who=f"{getpass.getuser()}@{socket.gethostname()}",
        )
    except HoldoutRefused as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if cache is not None:
            cache.save()
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1 << 20 if sys.platform == "darwin" else 1 << 10
    )
    if cache is not None:
        print(f"S3: {cache.downloaded} arquivo(s) baixado(s), {cache.unchanged} sem mudança")
    print(f"dataset {built.dataset_id} (trilha {args.track}) · minutos: {built.minutes}")
    for h, n in built.eligible.items():
        line = f"  H = {h} min: {n} pontos elegíveis ({n / max(built.minutes, 1):.1%})"
        if built.positives is not None:
            line += f" · alvo positivo em {built.positives[h] / max(n, 1):.1%}"
        print(line)
    if built.positives is None:
        print("avaliação final: registro de acesso gravado; sem estatísticas do alvo (RF-228)")
    for warning in built.warnings:
        print(f"aviso: {warning}")
    print(f"tempo: {time.monotonic() - began:.1f} s · pico de memória: {peak_mb:.0f} MB")
    print(f"→ {built.path} e derived/datasets/{built.dataset_id}/")
    return 0


def _research_store(config: PipelineConfig) -> LocalDirectoryStore | S3ObjectStore:
    """Onde ficam os datasets e os registros: o S3 do projeto ou, sem ele, um diretório."""
    if config.replica_s3_bucket:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        return S3ObjectStore(client, config.replica_s3_bucket)
    return LocalDirectoryStore(config.replica_dir or config.data_dir)


def _who() -> str:
    import getpass
    import socket

    return f"{getpass.getuser()}@{socket.gethostname()}"


def _peak_mb() -> float:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1 << 20 if sys.platform == "darwin" else 1 << 10)


def run_baselines(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Baselines B0 a B3 de um dataset, com registro (doc 03 §5.3; RF-257). O `numpy` só é
    importado aqui (RNF-029)."""
    try:
        from trader.backtest.simulator import REPORTED
        from trader.experiments.registry import Registry
        from trader.pipeline.baselines import BaselineRequest
        from trader.pipeline.baselines import run_baselines as run
        from trader.pipeline.datasets import code_version
    except ImportError as exc:
        print(f"falta o grupo de pesquisa (numpy): {exc}", file=sys.stderr)
        return 2

    began = time.monotonic()
    store = _research_store(config)
    code = code_version()
    if code.endswith(".dirty"):
        print(f"aviso: código com mudanças sem commit ({code})", file=sys.stderr)
    request = BaselineRequest(
        args.dataset,
        args.horizon,
        args.start,
        args.end,
        args.b2_trades,
        args.seed,
        args.allow_holdout,
    )
    with Registry(args.registry, store) as registry:
        try:
            report = run(
                request,
                datasets_dir=args.datasets_dir,
                fetcher=store,
                registry=registry,
                code=code,
                who=_who(),
                clock=lambda: datetime.now(UTC),
            )
        except HoldoutRefused as exc:
            print(f"recusado: {exc}", file=sys.stderr)
            return 2
        except (ValueError, StorageError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print(
        f"execução {report.execution_id} · dataset {args.dataset} (trilha {report.track})"
        f" · H = {args.horizon} min · {report.window.label}"
    )
    header = "operações  expect. (pb)  retorno  Sharpe  drawdown  acerto  exposição"
    print(f"{'':22}{header}")
    for rung, by_scenario in report.metrics.items():
        for name in REPORTED:
            m = by_scenario[name]
            sharpe = "—" if m.sharpe is None else f"{m.sharpe:6.2f}"
            hit = "—" if m.hit_rate is None else f"{m.hit_rate:.1%}"
            print(
                f"{rung} {name:<18} {m.trades:9d} {m.expectancy_bps:12.1f} "
                f"{m.total_return:8.2%} {sharpe:>7} {m.max_drawdown:9.2%} {hit:>7} "
                f"{m.exposure:9.1%}"
            )
    b2 = report.b2_summary()
    if b2 is not None:
        print(
            f"B2 ({args.b2_trades} operações, {b2['simulations']:.0f} simulações, semente"
            f" {args.seed}, base): retorno total p5 {b2['p05']:.2%} · mediana {b2['p50']:.2%}"
            f" · p95 {b2['p95']:.2%}"
        )
    print("sensibilidade (sem deslizamento, deslizamento alto, latência de 2 s): no registro")
    parts = " · ".join(f"{name} {seconds:.1f} s" for name, seconds in report.seconds.items())
    print(
        f"tempo: {time.monotonic() - began:.1f} s ({parts}) · pico de memória: {_peak_mb():.0f} MB"
    )
    print(f"→ {args.registry} e derived/experiments/{report.execution_id}.*.json")
    return 0


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _names(text))


def run_walkforward(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Walk-forward da etapa 6, uma execução por degrau, horizonte e modelo (RF-247). `numpy`,
    `scikit-learn` e `xgboost` só são importados aqui (RNF-029)."""
    try:
        from trader.experiments.registry import Registry
        from trader.pipeline.datasets import code_version
        from trader.pipeline.walkforward import TaskResult, WalkForwardRequest
        from trader.pipeline.walkforward import run_walkforward as run
    except ImportError as exc:
        print(f"falta o grupo de pesquisa (numpy, scikit-learn, xgboost): {exc}", file=sys.stderr)
        return 2

    began = time.monotonic()
    store = _research_store(config)
    code = code_version()
    if code.endswith(".dirty"):
        print(f"aviso: código com mudanças sem commit ({code})", file=sys.stderr)
    request = WalkForwardRequest(args.dataset, args.rungs, args.horizons, args.models, args.seed)

    def show(done: TaskResult) -> None:
        t = done.task
        print(
            f"{done.execution_id} · {t.rung} · H = {t.horizon} min · {t.model}"
            f" · {done.seconds:.0f} s",
            flush=True,
        )
        if done.result is None:
            print(f"  erro: {done.error}", flush=True)
            return
        for f in done.result.folds:
            params = " ".join(f"{k}={v}" for k, v in done.result.grid[f.chosen].items())
            base = f.test.paths["base"]
            if f.abstained:
                choice = "abstenção"
            else:
                choice = f"τ {f.tau:.2f} · {len(base.returns)} operações"
            ret = float(base.equity[-1]) - 1
            print(f"  {f.fold.name} {f.fold.test.label}  {params}  {choice}  {ret:+.2%}")
        for name, m in done.result.combined().items():
            if name not in ("base", "estresse"):
                continue
            sharpe = "—" if m.sharpe is None else f"{m.sharpe:.2f}"
            print(
                f"  dobras juntas, {name}: {m.trades} operações · {m.expectancy_bps:.1f} pb"
                f" · retorno {m.total_return:+.2%} · Sharpe {sharpe}"
                f" · drawdown {m.max_drawdown:.2%}",
                flush=True,
            )

    with Registry(args.registry, store) as registry:
        try:
            results = run(
                request,
                datasets_dir=args.datasets_dir,
                fetcher=store,
                registry=registry,
                code=code,
                who=_who(),
                clock=lambda: datetime.now(UTC),
                on_result=show,
            )
        except HoldoutRefused as exc:
            print(f"recusado: {exc}", file=sys.stderr)
            return 2
        except (ValueError, StorageError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        failed = [r for r in results if r.error]
        print(
            f"{len(results) - len(failed)} de {len(results)} execuções ok"
            f" · tempo: {time.monotonic() - began:.0f} s · pico de memória: {_peak_mb():.0f} MB"
        )
    print(f"→ {args.registry}, derived/experiments/<id>.*.json e <id>/predictions.parquet")
    return 1 if failed else 0


def run_report(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Relatório de desenvolvimento a partir do registro e das previsões (RF-282)."""
    try:
        from trader.experiments.registry import Registry
        from trader.pipeline.datasets import code_version
        from trader.pipeline.report import build_report as build
    except ImportError as exc:
        print(f"falta o grupo de pesquisa (numpy): {exc}", file=sys.stderr)
        return 2

    began = time.monotonic()
    store = _research_store(config)
    with Registry(args.registry, store) as registry:
        try:
            report = build(
                args.dataset,
                datasets_dir=args.datasets_dir,
                fetcher=store,
                store=store,
                registry=registry,
                reports_dir=args.reports_dir,
                seed=args.seed,
                code=code_version(),
                clock=lambda: datetime.now(UTC),
            )
        except HoldoutRefused as exc:
            print(f"recusado: {exc}", file=sys.stderr)
            return 2
        except (ValueError, StorageError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print(report.summary)
    print(f"tempo: {time.monotonic() - began:.1f} s · pico de memória: {_peak_mb():.0f} MB")
    print(f"→ {report.path} e {report.key}")
    return 0


def run_experiments(config: PipelineConfig, args: argparse.Namespace) -> int:
    """Consulta e manutenção do registro de experimentos (RF-276). Não usa `numpy`."""
    from trader.experiments.registry import EXPERIMENTS, Registry, rebuild

    store = _research_store(config)
    if args.action == "rebuild":
        downloads = args.registry.parent / "records-s3"
        records = []
        for key in store.list_keys(EXPERIMENTS + "/"):
            name = key.removeprefix(EXPERIMENTS + "/")
            if "/" in name or not name.endswith(".json"):
                continue  # arquivos auxiliares das execuções, como as previsões
            local = downloads / name
            store.fetch(key, local)
            records.append(json.loads(local.read_text()))
        with rebuild(args.registry, records, store) as registry:
            print(
                f"registro refeito com {len(records)} registros: {len(registry.executions())}"
                " execuções"
            )
        return 0
    with Registry(args.registry, store) as registry:
        try:
            if args.action == "list":
                for row in registry.executions(args.track):
                    mark = " (descartada)" if row["discarded_at"] else ""
                    print(
                        f"{row['id']}  {row['kind']:<8} trilha {row['track']}  {row['status']:<7}"
                        f" dataset {row['dataset_id']}{mark}"
                    )
            elif args.action == "count":
                for track in [args.track] if args.track else ["A", "B"]:
                    print(f"trilha {track}: {registry.count(track)} tentativas (IS-15)")
            elif args.execution_id is None:
                print(f"{args.action} precisa do id da execução", file=sys.stderr)
                return 2
            elif args.action == "show":
                print(json.dumps(registry.show(args.execution_id), indent=1, ensure_ascii=False))
            else:
                registry.discard(args.execution_id, args.reason or "", datetime.now(UTC))
                print(f"{args.execution_id} descartada: {args.reason}")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


def quality_report(config: PipelineConfig, start: date, end: date, json_path: Path | None) -> int:
    if start > end:
        print(f"início {start} depois do fim {end}", file=sys.stderr)
        return 2
    db = config.data_dir / "state.db"
    if not db.exists():
        print(
            f"sem {db}: lacunas e sessões seriam omitidas. Em uma cópia do S3, use"
            " ops/state-backup.db como state.db",
            file=sys.stderr,
        )
        return 2
    with StateStore(db) as state:
        report = build_report(config.data_dir, state, config.symbol, start, end)
    print("\n".join(report.lines()))
    if json_path is not None:
        json_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0


def dashboard_data(config: PipelineConfig, out_dir: Path) -> int:
    if config.replica_s3_bucket:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        synced = sync_from_s3(client, config.replica_s3_bucket, config.data_dir, config.symbol)
        print(
            f"S3: {synced.downloaded} arquivo(s) baixado(s), {synced.unchanged} sem mudança,"
            f" {synced.raw_counted} arquivo(s) bruto(s) contado(s)"
        )
    try:
        report = export(config.data_dir, out_dir, config.symbol)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"coleta desde {report.collection_start}")
    for name, count in report.rows.items():
        print(f"  {name}: {count} linha(s)")
    return 0


def publish_status(config: PipelineConfig, out_dir: Path | None, *, once: bool = False) -> int:
    if out_dir is None and not config.public_bucket:
        print("sem destino: defina public_bucket em [pipeline] ou use --out", file=sys.stderr)
        return 2
    session = None
    if config.replica_s3_bucket or out_dir is None:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        session = boto3.session.Session(region_name=config.aws_region)
    data_s3 = session.client("s3") if session and config.replica_s3_bucket else None
    sink: Sink
    if out_dir is not None:
        sink = DirectorySink(out_dir)
    else:
        assert session is not None and config.public_bucket
        sink = S3Sink(session.client("s3"), config.public_bucket)
    publisher = StatusPublisher(config, sink, data_s3)
    if once:
        publisher.tick(time.time_ns() // 1000)
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    publisher.run(stop)
    return 0


def _print_backfill(report: BackfillReport, *, replicated: bool) -> None:
    imported = report.with_status("imported")
    rejected = report.with_status("rejected")
    missing = report.with_status("missing")
    print(
        f"arquivos: importados {len(imported)} · já importados {report.skipped}"
        f" · rejeitados {len(rejected)} · ausentes {len(missing)}"
    )
    print(
        f"linhas: {sum(f.rows for f in imported)}"
        f" · quarentena: {sum(f.quarantined for f in imported)}"
        f" · repetições: {sum(f.duplicates for f in imported)}"
        f" · ids ausentes: {sum(f.missing_ids for f in imported)}"
        f" · minutos sem candle: {sum(f.missing_minutes for f in imported)}"
    )
    outputs = sum(len(f.outputs) for f in imported)
    print(f"réplica: {f'{outputs} arquivo(s)' if replicated else 'desligada'}")
    for label, rows in (("rejeitados (checksum)", rejected), ("ausentes (404)", missing)):
        if rows:
            print(f"{label}: " + ", ".join(f.file_name for f in rows))


async def smoke_test(config: CollectorConfig, duration_s: float) -> int:
    async with httpx.AsyncClient(timeout=10.0) as client:
        report = await run_smoke(config, websockets_connector, duration_s, client)
    print("\n".join(report.lines))
    print("resultado:", "OK" if report.ok else f"{len(report.problems)} problema(s)")
    return 0 if report.ok else 1


def _object_store(config: PipelineConfig) -> ObjectStore | None:
    if config.replica_s3_bucket:
        import boto3  # só o pipeline usa o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        return S3ObjectStore(client, config.replica_s3_bucket)
    if config.replica_dir:
        return LocalDirectoryStore(config.replica_dir)
    return None


def _metrics(host: str | None, port: int) -> Metrics:
    return StatsdMetrics(host, port) if host else NullMetrics()


def _hour_label(hour: int) -> str:
    return time.strftime("%Y-%m-%d %H:00 UTC", time.gmtime(hour * HOUR_US / 1e6))


if __name__ == "__main__":
    raise SystemExit(main())
