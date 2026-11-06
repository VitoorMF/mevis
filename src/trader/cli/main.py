"""Linha de comando: `collect`, `collector-ready`, `status`, `pipeline`, `reconcile`, `backfill`,
`aggregates`, `quality`, `dashboard-data`, `status-publisher` e `smoke` (RF-181 a RF-187)."""

import argparse
import asyncio
import json
import re
import signal
import sys
import threading
import time
from collections.abc import Sequence
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

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
from trader.pipeline.aggregates import AggregatesJob, HoldoutRefused, JobOptions, job_store
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
    "quality",
    "dashboard-data",
    "status-publisher",
)
"""Comandos configurados pela tabela `[pipeline]`."""


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
