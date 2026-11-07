"""Agregados por minuto (doc 06 §5.6): horas ao vivo no pipeline horário e períodos sob demanda
(doc 03 §7.8; RF-172 a RF-179, RF-187).

- `aggregate_live_hour`: uma hora normalizada pelo pipeline, com o status da máscara.
- `AggregatesJob`: um período, lendo do armazenamento durável dia a dia; é o que roda na instância
  temporária (`scripts/run-job.sh`) ou, no plano B, no MacBook. Recusa o holdout da trilha A sem
  `allow_holdout` (RF-179), confere a continuidade dos ids entre dias de arquivo (RF-176), grava
  um manifesto com entradas e saídas e seus SHA-256 (RF-187) e pode ser repetido: regrava as
  mesmas chaves com o mesmo conteúdo (RF-173).
"""

import json
import logging
import multiprocessing
import resource
import socket
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.aggregate.book import COLUMNS as BOOK_COLUMNS
from trader.aggregate.book import minute_book
from trader.aggregate.trades import (
    COLUMNS as TRADE_COLUMNS,
)
from trader.aggregate.trades import (
    MISSING,
    TradeEdge,
    TradeMinutes,
    boundary_span,
    mark_missing,
)
from trader.core.holdout import in_holdout, refuse_holdout
from trader.core.timeunits import MINUTE_US
from trader.ops.config import PipelineConfig
from trader.ops.logs import ctx
from trader.storage import parquet
from trader.storage.layout import (
    HOUR_US,
    archive_dataset_path,
    dataset_path,
    relative,
    replace_atomically,
)
from trader.storage.objectstore import LocalDirectoryStore, S3ObjectStore
from trader.storage.rawfiles import file_sha256

log = logging.getLogger(__name__)

DAY_US = 86_400_000_000
TRADE_BATCH_ROWS = 100_000
JOBS_PREFIX = "derived/ops/jobs"
_EPOCH = date(1970, 1, 1)


def write_table(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    replace_atomically(tmp, path)


def day_start_us(day: date) -> int:
    return (day - _EPOCH).days * DAY_US


def _trade_minutes(path: Path, batch_rows: int) -> TradeMinutes:
    minutes = TradeMinutes()
    if path.exists():
        source = pq.ParquetFile(path)
        for batch in source.iter_batches(batch_size=batch_rows, columns=list(TRADE_COLUMNS)):
            minutes.add(batch)
    return minutes


# ------------------------------------------------------------------------------ hora ao vivo


@dataclass(frozen=True)
class LiveHourResult:
    outputs: list[Path]
    trades: int
    snapshots: int


def aggregate_live_hour(
    data_dir: Path,
    symbol: str,
    hour: int,
    *,
    trade_channel: str,
    top20_channel: str,
    batch_rows: int = TRADE_BATCH_ROWS,
) -> LiveHourResult:
    """Agrega uma hora já normalizada; o status de cada minuto vem da máscara da hora."""
    statuses: dict[str, dict[int, str]] = {}
    mask = pq.read_table(
        dataset_path(data_dir, "derived", "quality_mask", symbol, hour),
        columns=["channel", "minute_ts_us", "status"],
    )
    for row in mask.to_pylist():
        statuses.setdefault(row["channel"], {})[row["minute_ts_us"]] = row["status"]
    start, end = hour * HOUR_US, (hour + 1) * HOUR_US

    trades_in = dataset_path(data_dir, "normalized", "trades", symbol, hour)
    trades = _trade_minutes(trades_in, batch_rows)
    trades_out = dataset_path(data_dir, "derived", "minute_trades", symbol, hour)
    write_table(
        trades_out,
        trades.table(symbol, start, end, source="live", statuses=statuses.get(trade_channel, {})),
    )

    book_path = dataset_path(data_dir, "normalized", "book_top20", symbol, hour)
    if book_path.exists():
        snapshots = pq.read_table(book_path, columns=list(BOOK_COLUMNS))
    else:
        snapshots = parquet.BOOK_TOP20.empty_table().select(list(BOOK_COLUMNS))
    book_out = dataset_path(data_dir, "derived", "minute_book", symbol, hour)
    write_table(
        book_out,
        minute_book(snapshots, symbol, start, end, statuses=statuses.get(top20_channel, {})),
    )
    return LiveHourResult([trades_out, book_out], trades.rows, snapshots.num_rows)


# ------------------------------------------------------------------------------ dia de arquivo


def aggregate_archive_day(
    data_dir: Path, symbol: str, day: date, batch_rows: int = TRADE_BATCH_ROWS
) -> tuple[Path, TradeEdge | None, TradeEdge | None, int]:
    """Agrega um dia importado pelo backfill, com o status pela continuidade dos ids dentro do
    dia. Devolve a saída e o primeiro e o último negócio, para a conferência entre dias."""
    source = archive_dataset_path(data_dir, "trades", symbol, day)
    trades = _trade_minutes(source, batch_rows)
    start = day_start_us(day)
    statuses = trades.archive_statuses(start, start + DAY_US)
    output = archive_dataset_path(data_dir, "minute_trades", symbol, day, layer="derived")
    write_table(
        output, trades.table(symbol, start, start + DAY_US, source="archive", statuses=statuses)
    )
    return output, trades.first_edge(), trades.last_edge(), trades.rows


def archive_edges(path: Path) -> tuple[TradeEdge, TradeEdge] | None:
    """Primeiro e último negócio de um dia de arquivo, lendo só o primeiro e o último bloco (o
    backfill grava em ordem de id, doc 06 §7)."""
    source = pq.ParquetFile(path)
    if source.metadata.num_rows == 0:
        return None
    columns = ["trade_id", "trade_ts_us"]
    edges = []
    for group, pick in ((0, pc.min), (source.num_row_groups - 1, pc.max)):
        table = source.read_row_group(group, columns=columns)
        trade_id = pick(table["trade_id"]).as_py()
        index = pc.index(table["trade_id"], trade_id).as_py()
        ts = table["trade_ts_us"][index].as_py()
        edges.append(TradeEdge(trade_id, ts - ts % MINUTE_US))
    return edges[0], edges[1]


# ------------------------------------------------------------------------------ job sob demanda


class JobStore(Protocol):
    def list_keys(self, prefix: str) -> list[str]: ...

    def fetch(self, key: str, local: Path) -> str: ...

    def put(self, local: Path, key: str, sha256: str) -> None: ...


class InPlaceStore:
    """Sem réplica: as entradas já estão no diretório de dados e as saídas ficam nele."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_keys(self, prefix: str) -> list[str]:
        return LocalDirectoryStore(self.root).list_keys(prefix)

    def fetch(self, key: str, local: Path) -> str:
        return file_sha256(local)

    def put(self, local: Path, key: str, sha256: str) -> None:
        return None


def job_store(config: PipelineConfig) -> JobStore:
    if config.replica_s3_bucket:
        import boto3  # só o pipeline e a CLI usam o SDK da AWS (ADR-03)

        client = boto3.session.Session(region_name=config.aws_region).client("s3")
        return S3ObjectStore(client, config.replica_s3_bucket)
    if config.replica_dir:
        return LocalDirectoryStore(config.replica_dir)
    return InPlaceStore(config.data_dir)


@dataclass(frozen=True)
class JobOptions:
    start: date
    end: date
    job_id: str
    code_version: str
    allow_holdout: bool = False
    workers: int = 1
    batch_rows: int = TRADE_BATCH_ROWS


@dataclass(frozen=True)
class _Unit:
    kind: str
    """`archive` (um dia) ou `live` (uma hora)."""
    day: date
    hour: int | None
    inputs: tuple[str, ...]


@dataclass
class _UnitResult:
    unit: _Unit
    inputs: list[dict[str, Any]]
    outputs: list[str]
    trades: int
    seconds: float
    max_rss_mb: float
    first: TradeEdge | None = None
    last: TradeEdge | None = None


@dataclass
class JobReport:
    manifest: dict[str, Any]
    manifest_key: str
    warnings: list[str] = field(default_factory=list)


def _max_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1 << 20) if sys.platform == "darwin" else peak / 1024, 1)


def _run_unit(
    unit: _Unit, config: PipelineConfig, batch_rows: int, store: JobStore | None = None
) -> _UnitResult:
    """Processa um dia de arquivo ou uma hora ao vivo. Roda num processo à parte quando há mais
    de um trabalhador; por isso recebe a configuração e cria o próprio acesso ao S3."""
    began = time.monotonic()
    store = store if store is not None else job_store(config)
    data_dir, symbol = config.data_dir, config.symbol
    downloaded: list[Path] = []
    inputs = []
    try:
        for key in unit.inputs:
            local = data_dir / key
            sha256 = store.fetch(key, local)
            if not isinstance(store, InPlaceStore):
                downloaded.append(local)
            inputs.append({"key": key, "sha256": sha256, "bytes": local.stat().st_size})
        first = last = None
        if unit.kind == "archive":
            output, first, last, trades = aggregate_archive_day(
                data_dir, symbol, unit.day, batch_rows
            )
            outputs = [output]
        else:
            assert unit.hour is not None
            trade_channel, _, top20_channel, _ = config.streams
            live = aggregate_live_hour(
                data_dir,
                symbol,
                unit.hour,
                trade_channel=trade_channel,
                top20_channel=top20_channel,
                batch_rows=batch_rows,
            )
            outputs, trades = live.outputs, live.trades
    finally:
        for path in downloaded:
            path.unlink(missing_ok=True)
    return _UnitResult(
        unit=unit,
        inputs=inputs,
        outputs=[relative(data_dir, p) for p in outputs],
        trades=trades,
        seconds=round(time.monotonic() - began, 2),
        max_rss_mb=_max_rss_mb(),
        first=first,
        last=last,
    )


class AggregatesJob:
    """Agregados de `[start, end]`: dias de arquivo e horas ao vivo que existirem no período."""

    def __init__(
        self,
        config: PipelineConfig,
        store: JobStore,
        options: JobOptions,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._store = store
        self._options = options
        self._clock = clock
        self._data_dir = config.data_dir
        self._symbol = config.symbol
        self._warnings: list[str] = []

    def run(self) -> JobReport:
        options = self._options
        if options.start > options.end:
            raise ValueError(f"início {options.start} depois do fim {options.end}")
        reserved = refuse_holdout(
            options.start, options.end, allow=options.allow_holdout, requirement="RF-179"
        )
        started = self._clock()
        manifest: dict[str, Any] = {
            "job_id": options.job_id,
            "command": "aggregates",
            "code_version": options.code_version,
            "args": {
                "start": options.start.isoformat(),
                "end": options.end.isoformat(),
                "allow_holdout": options.allow_holdout,
                "workers": options.workers,
                "batch_rows": options.batch_rows,
            },
            "holdout_days": len(reserved),
            "symbol": self._symbol,
            "host": socket.gethostname(),
            "started_utc": _iso(started),
            "status": "running",
        }
        key = f"{JOBS_PREFIX}/{options.job_id}/manifest.json"
        try:
            self._execute(manifest)
        except BaseException as exc:
            manifest["status"] = "failed"
            manifest["error"] = repr(exc)
            self._finish(manifest, started)
            try:
                self._publish_manifest(key, manifest)
            except Exception as publish_error:  # não esconde a falha original
                log.error("manifesto não gravado", extra=ctx(error=repr(publish_error)))
            raise
        manifest["status"] = "ok"
        self._finish(manifest, started)
        self._publish_manifest(key, manifest)
        return JobReport(manifest, key, self._warnings)

    def _finish(self, manifest: dict[str, Any], started: float) -> None:
        finished = self._clock()
        manifest["finished_utc"] = _iso(finished)
        manifest["duration_s"] = round(finished - started, 1)
        manifest["warnings"] = self._warnings

    def _execute(self, manifest: dict[str, Any]) -> None:
        units = self._plan()
        log.info(
            "agregados: início",
            extra=ctx(
                archive_days=sum(u.kind == "archive" for u in units),
                live_hours=sum(u.kind == "live" for u in units),
                workers=self._options.workers,
            ),
        )
        results = sorted(self._run_units(units), key=lambda r: (r.unit.day, r.unit.hour or -1))
        edge_inputs = self._check_boundaries(results)
        outputs = []
        for result in results:
            for output in result.outputs:
                path = self._data_dir / output
                sha256 = file_sha256(path)
                self._store.put(path, output, sha256)
                outputs.append(
                    {"key": output, "sha256": sha256, "rows": pq.read_metadata(path).num_rows}
                )
                if not isinstance(self._store, InPlaceStore):
                    path.unlink()
        manifest["units"] = [
            {
                "kind": r.unit.kind,
                "day": r.unit.day.isoformat(),
                "hour": None if r.unit.hour is None else r.unit.hour % 24,
                "trades": r.trades,
                "seconds": r.seconds,
                "max_rss_mb": r.max_rss_mb,
            }
            for r in results
        ]
        manifest["inputs"] = [i for r in results for i in r.inputs] + edge_inputs
        manifest["outputs"] = outputs
        manifest["trades"] = sum(r.trades for r in results)
        manifest["max_rss_mb"] = max((r.max_rss_mb for r in results), default=0.0)

    def _plan(self) -> list[_Unit]:
        units = []
        day = self._options.start
        while day <= self._options.end:
            date_dir = f"symbol={self._symbol}/date={day:%Y-%m-%d}/"
            trades = self._store.list_keys(f"normalized/trades/{date_dir}")
            books = set(self._store.list_keys(f"normalized/book_top20/{date_dir}"))
            masks = self._store.list_keys(f"derived/quality_mask/{date_dir}")
            archive = f"normalized/trades/{date_dir}archive.parquet"
            if archive in trades:
                units.append(_Unit("archive", day, None, (archive,)))
            for mask in masks:
                name = mask.rsplit("/", 1)[1]
                hour = (day - _EPOCH).days * 24 + int(name.removesuffix(".parquet"))
                inputs = [mask]
                inputs += [k for k in (f"normalized/trades/{date_dir}{name}",) if k in trades]
                inputs += [k for k in (f"normalized/book_top20/{date_dir}{name}",) if k in books]
                units.append(_Unit("live", day, hour, tuple(inputs)))
            if archive not in trades and not masks:
                self._warnings.append(f"{day}: sem negócios normalizados")
            day += timedelta(days=1)
        return units

    def _run_units(self, units: list[_Unit]) -> Iterable[_UnitResult]:
        options = self._options
        if options.workers <= 1:
            for unit in units:
                result = _run_unit(unit, self._config, options.batch_rows, self._store)
                _log_unit(result)
                yield result
            return
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=options.workers, mp_context=context) as pool:
            futures: list[Future[_UnitResult]] = [
                pool.submit(_run_unit, unit, self._config, options.batch_rows) for unit in units
            ]
            try:
                for future in futures:
                    result = future.result()
                    _log_unit(result)
                    yield result
            except BaseException:
                pool.shutdown(wait=True, cancel_futures=True)
                raise

    def _check_boundaries(self, results: list[_UnitResult]) -> list[dict[str, Any]]:
        """Continuidade dos ids entre dias de arquivo (RF-176): entre dias do período e com os
        vizinhos de fora, quando existem e não são do holdout."""
        days = {r.unit.day: r for r in results if r.unit.kind == "archive"}
        edge_inputs: list[dict[str, Any]] = []
        for day in sorted(days):
            result = days[day]
            previous = days.get(day - timedelta(days=1))
            if previous is not None and previous.last and result.first:
                span = boundary_span(previous.last, result.first)
                if span is not None:
                    self._mark(previous, span)
                    self._mark(result, span)
                    self._warnings.append(f"salto de id entre {previous.unit.day} e {day}")
        if days:
            first_day, last_day = min(days), max(days)
            before = self._neighbor(first_day - timedelta(days=1), edge_inputs)
            head = days[first_day].first
            if before is not None and head is not None:
                span = boundary_span(before[1], head)
                if span is not None:
                    self._mark(days[first_day], span)
                    self._warnings.append(
                        f"salto de id entre {first_day - timedelta(days=1)} e {first_day}; os"
                        f" minutos de {first_day - timedelta(days=1)} não foram remarcados"
                    )
            after = self._neighbor(last_day + timedelta(days=1), edge_inputs)
            tail = days[last_day].last
            if after is not None and tail is not None:
                span = boundary_span(tail, after[0])
                if span is not None:
                    self._mark(days[last_day], span)
                    self._warnings.append(
                        f"salto de id entre {last_day} e {last_day + timedelta(days=1)}; os"
                        f" minutos de {last_day + timedelta(days=1)} não foram remarcados"
                    )
        return edge_inputs

    def _neighbor(
        self, day: date, edge_inputs: list[dict[str, Any]]
    ) -> tuple[TradeEdge, TradeEdge] | None:
        if in_holdout(day) and not self._options.allow_holdout:
            self._warnings.append(f"{day}: vizinho no holdout, fronteira não conferida")
            return None
        key = f"normalized/trades/symbol={self._symbol}/date={day:%Y-%m-%d}/archive.parquet"
        if key not in self._store.list_keys(key):
            return None
        local = self._data_dir / key
        sha256 = self._store.fetch(key, local)
        try:
            edge_inputs.append(
                {"key": key, "sha256": sha256, "bytes": local.stat().st_size, "edge_only": True}
            )
            return archive_edges(local)
        finally:
            if not isinstance(self._store, InPlaceStore):
                local.unlink(missing_ok=True)

    def _mark(self, result: _UnitResult, span: tuple[int, int]) -> None:
        path = self._data_dir / result.outputs[0]
        table = mark_missing(pq.read_table(path), *span)
        write_table(path, table)
        missing = pc.sum(pc.cast(pc.equal(table["status"], MISSING), pa.int64())).as_py()
        log.warning(
            "salto de id entre dias",
            extra=ctx(day=result.unit.day.isoformat(), missing_minutes=missing),
        )

    def _publish_manifest(self, key: str, manifest: dict[str, Any]) -> None:
        path = self._data_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        self._store.put(path, key, file_sha256(path))


def _log_unit(result: _UnitResult) -> None:
    unit = result.unit
    label = unit.day.isoformat() if unit.hour is None else f"{unit.day} {unit.hour % 24:02d}h"
    log.info(
        "agregados: unidade concluída",
        extra=ctx(
            kind=unit.kind,
            unit=label,
            trades=result.trades,
            seconds=result.seconds,
            max_rss_mb=result.max_rss_mb,
        ),
    )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")
