"""Linha de comando: `trader collect`, `trader status` e `trader pipeline` (RF-181 a RF-185)."""

import argparse
import asyncio
import signal
import sys
import time
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

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
from trader.ops.health import health_path, read_health
from trader.ops.logs import configure_logging
from trader.ops.metrics import Metrics, NullMetrics, StatsdMetrics
from trader.ops.state import StateStore
from trader.pipeline.hourly import HourlyPipeline, PipelineBusy
from trader.sources.binance_rest import BinanceRest, WeightLimiter
from trader.sources.binance_ws import websockets_connector
from trader.storage.layout import HOUR_US
from trader.storage.objectstore import LocalDirectoryStore

STALE_HEALTH_S = 30


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trader")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="executa o coletor contínuo")
    collect.add_argument("--config", type=Path, help="arquivo TOML com a tabela [collector]")
    collect.add_argument("--data-dir", type=Path, help="diretório de dados (padrão: ./data)")
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

    args = parser.parse_args(argv)
    try:
        if args.command == "pipeline":
            pipeline_config = load_pipeline_config(
                args.config, data_dir=args.data_dir, replica_dir=args.replica_dir
            )
        else:
            config = load_config(args.config, data_dir=args.data_dir)
    except ConfigError as exc:
        print(f"configuração inválida: {exc}", file=sys.stderr)
        return 2
    if args.command == "pipeline":
        configure_logging(args.log_level)
        return run_pipeline(pipeline_config, rebuild=args.rebuild)
    if args.command == "collect":
        configure_logging(args.log_level)
        asyncio.run(run_collector(config, args.duration))
        return 0
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
    metrics: Metrics = (
        StatsdMetrics(config.statsd_host, config.statsd_port)
        if config.statsd_host
        else NullMetrics()
    )
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
    health = read_health(health_path(config.data_dir))
    if health is None:
        print(f"sem arquivo de saúde em {health_path(config.data_dir)}")
        return 1
    now_us = time.time_ns() // 1000
    age_s = (now_us - int(health["updated_ts_us"])) / 1e6
    running = health["status"] == "running" and age_s <= STALE_HEALTH_S
    print(f"coletor: {health['status']} (atualizado há {age_s:.0f} s) — versão {health['version']}")
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
    state_path = config.data_dir / "state.db"
    recent = []
    if state_path.exists():
        with StateStore(state_path) as state:
            recent = state.gaps_since(now_us - 24 * 3600 * 1_000_000)
    if recent:
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
    return 0 if running else 1


if __name__ == "__main__":
    raise SystemExit(main())


def run_pipeline(config: PipelineConfig, *, rebuild: bool = False) -> int:
    store = LocalDirectoryStore(config.replica_dir) if config.replica_dir else None
    with StateStore(config.data_dir / "state.db") as state:
        try:
            report = HourlyPipeline(config, state, store).run(
                time.time_ns() // 1000, rebuild=rebuild
            )
        except PipelineBusy as exc:
            print(str(exc), file=sys.stderr)
            return 3
    hours = ", ".join(_hour_label(h) for h in report.normalized_hours) or "nenhuma"
    print(f"órfãos fechados: {report.sealed_orphans} · comprimidos: {report.compressed}")
    print(f"horas normalizadas: {hours}")
    if report.normalized_hours:
        rows = " · ".join(f"{k}: {v}" for k, v in sorted(report.rows.items()))
        duplicates = sum(report.duplicates.values())
        print(f"linhas: {rows} · duplicatas descartadas: {duplicates}")
        print(f"quarentena: {report.quarantined}")
    replica = "desligada" if store is None else f"{report.replicated} arquivo(s)"
    print(f"réplica: {replica} · removidos pela retenção: {report.deleted}")
    return 0


def _hour_label(hour: int) -> str:
    return time.strftime("%Y-%m-%d %H:00 UTC", time.gmtime(hour * HOUR_US / 1e6))
