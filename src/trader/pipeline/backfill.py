"""Backfill dos arquivos públicos (doc 06 §7; RF-141 a RF-144 e RF-149).

Para cada arquivo do período: baixa o ZIP e o `.CHECKSUM`, confere o SHA-256, normaliza o CSV
em fluxo com o mesmo código dos dados ao vivo, grava um Parquet por dia, replica e registra o
resultado em `backfill_files`. Um arquivo só conta como importado depois de gravado e
replicado; se for interrompido, é refeito do início e regrava as mesmas saídas, sem duplicar.
"""

import csv
import fcntl
import io
import logging
import os
import time
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trader.core.models import Kline, Origin, Trade
from trader.core.timeunits import archive_unit
from trader.normalize.binance import NormalizationError, parse_archive_trade, parse_rest_kline
from trader.ops.config import PipelineConfig
from trader.ops.logs import ctx
from trader.ops.state import BackfillFileRow, StateStore
from trader.quality.validation import REJECTING_ISSUES, Issue, validate_kline, validate_trade
from trader.sources.binance_archive import (
    ArchiveClient,
    ArchiveError,
    ArchiveFile,
    ArchiveNotFound,
    plan_files,
)
from trader.storage import parquet
from trader.storage.layout import archive_dataset_path, archive_quarantine_path, relative
from trader.storage.objectstore import ObjectStore
from trader.storage.quarantine import QuarantineWriter
from trader.storage.rawfiles import file_sha256

log = logging.getLogger(__name__)

DAY_US = 86_400_000_000
DAY_MINUTES = 1440
ROW_GROUP_ROWS = 100_000
_EPOCH = date(1970, 1, 1)


class BackfillBusy(RuntimeError):
    """Outra execução do backfill está em andamento no mesmo diretório de dados."""


@dataclass
class BackfillReport:
    files: list[BackfillFileRow] = field(default_factory=list)
    skipped: int = 0
    """Arquivos já importados em execuções anteriores."""

    def with_status(self, status: str) -> list[BackfillFileRow]:
        return [f for f in self.files if f.status == status]


class Backfill:
    def __init__(
        self,
        config: PipelineConfig,
        state: StateStore,
        store: ObjectStore | None,
        archive: ArchiveClient,
        now_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self._config = config
        self._state = state
        self._store = store
        self._archive = archive
        self._now_us = now_us
        self._data_dir = config.data_dir

    def run(self, dataset: str, start: date, end: date) -> BackfillReport:
        report = BackfillReport()
        with self._lock():
            for archive in plan_files(dataset, self._config.symbol, start, end):
                if self._state.backfill_status(archive.name) == "imported":
                    report.skipped += 1
                    continue
                started = time.monotonic()
                row = self._process(archive)
                self._state.record_backfill_file(row)
                report.files.append(row)
                log.info(
                    "arquivo do backfill",
                    extra=ctx(
                        file=row.file_name,
                        status=row.status,
                        rows=row.rows,
                        quarantined=row.quarantined,
                        seconds=round(time.monotonic() - started, 1),
                    ),
                )
        return report

    def _process(self, archive: ArchiveFile) -> BackfillFileRow:
        zip_path = self._data_dir / "tmp" / "backfill" / archive.name
        try:
            try:
                expected = self._archive.checksum(archive)
                actual = self._archive.download(archive, zip_path)
            except ArchiveNotFound:
                return self._row(archive, "missing")
            if actual != expected:
                log.error(
                    "checksum divergente; arquivo rejeitado",
                    extra=ctx(file=archive.name, expected=expected, actual=actual),
                )
                return self._row(archive, "rejected", sha256=actual)
            imported = _DayFiles(self._config, archive, actual).run(zip_path)
        finally:
            zip_path.unlink(missing_ok=True)
        keys = self._replicate(imported.outputs)
        return self._row(
            archive,
            "imported",
            sha256=actual,
            rows=imported.rows,
            quarantined=imported.quarantined,
            duplicates=imported.duplicates,
            missing_ids=imported.missing_ids,
            missing_minutes=imported.missing_minutes,
            outputs=keys,
        )

    def _replicate(self, outputs: Sequence[Path]) -> tuple[str, ...]:
        """Envia as saídas ao armazenamento durável; com réplica, apaga a cópia local."""
        keys = []
        for path in outputs:
            key = relative(self._data_dir, path)
            if self._store is not None:
                self._store.put(path, key, file_sha256(path))
                path.unlink()
            keys.append(key)
        return tuple(keys)

    def _row(self, archive: ArchiveFile, status: str, **values: Any) -> BackfillFileRow:
        return BackfillFileRow(
            file_name=archive.name,
            dataset=archive.dataset,
            first_day=archive.first_day.isoformat(),
            last_day=archive.last_day.isoformat(),
            status=status,
            sha256=values.get("sha256"),
            rows=values.get("rows", 0),
            quarantined=values.get("quarantined", 0),
            duplicates=values.get("duplicates", 0),
            missing_ids=values.get("missing_ids", 0),
            missing_minutes=values.get("missing_minutes", 0),
            outputs=values.get("outputs", ()),
            finished_ts_us=self._now_us(),
        )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self._data_dir / "backfill.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackfillBusy("outra execução do backfill está em andamento") from exc
            yield
        finally:
            os.close(fd)


@dataclass
class _Imported:
    outputs: list[Path]
    rows: int
    quarantined: int
    duplicates: int
    missing_ids: int
    missing_minutes: int


class _DayFiles:
    """Normaliza um arquivo público e grava um Parquet (e uma quarentena) por dia."""

    def __init__(self, config: PipelineConfig, archive: ArchiveFile, sha256: str) -> None:
        self._data_dir = config.data_dir
        self._symbol = config.symbol
        self._archive = archive
        self._days = set(archive.days())
        self._unit = archive_unit(archive.first_day)
        self._provenance = f"{archive.name}@{sha256}"
        self._writers: dict[date, parquet.ChunkedParquetWriter] = {}
        self._quarantines: dict[date, QuarantineWriter] = {}
        self._last_trade: Trade | None = None
        self._klines: dict[int, Kline] = {}
        self._rows = 0
        self._duplicates = 0
        self._missing_ids = 0

    def run(self, zip_path: Path) -> _Imported:
        handle = self._trade if self._archive.dataset == "trades" else self._kline
        try:
            with zipfile.ZipFile(zip_path) as bundle:
                members = [n for n in bundle.namelist() if n.endswith(".csv")]
                if len(members) != 1:
                    raise ArchiveError(f"{self._archive.name}: esperado 1 CSV, há {members}")
                with bundle.open(members[0]) as raw:
                    text = io.TextIOWrapper(raw, encoding="ascii", errors="replace", newline="")
                    for number, fields in enumerate(csv.reader(text), 1):
                        if number == 1 and fields and not fields[0].isdigit():
                            continue  # cabeçalho, presente em alguns arquivos
                        handle(fields)
            outputs = [w.path for w in self._writers.values() if w.close()]
            outputs += [q.path for q in self._quarantines.values() if q.close()]
        except BaseException:
            for writer in self._writers.values():
                writer.abort()
            for quarantine in self._quarantines.values():
                quarantine.abort()
            raise
        missing_minutes = 0
        if self._archive.dataset == "klines_1m":
            missing_minutes = DAY_MINUTES * len(self._days) - len(self._klines)
        return _Imported(
            outputs=sorted(outputs),
            rows=self._rows,
            quarantined=sum(q.count for q in self._quarantines.values()),
            duplicates=self._duplicates,
            missing_ids=self._missing_ids,
            missing_minutes=missing_minutes,
        )

    def _trade(self, fields: list[str]) -> None:
        try:
            trade = parse_archive_trade(
                fields, symbol=self._symbol, unit=self._unit, provenance=self._provenance
            )
        except NormalizationError as exc:
            self._reject(self._archive.first_day, str(exc), fields)
            return
        day = _day(trade.trade_ts_us)
        if not self._accept(day, validate_trade(trade), fields):
            return
        last = self._last_trade
        if last is not None:
            if trade.trade_id == last.trade_id:
                if trade == last:
                    self._duplicates += 1
                else:
                    self._reject(day, "id repetido com conteúdo diferente", fields)
                return
            if trade.trade_id < last.trade_id:
                self._reject(day, "id fora de ordem", fields)
                return
            self._missing_ids += trade.trade_id - last.trade_id - 1
        self._last_trade = trade
        self._write(day, parquet.TRADES, parquet.trade_row(trade))

    def _kline(self, fields: list[str]) -> None:
        try:
            kline = parse_rest_kline(
                fields,
                symbol=self._symbol,
                interval="1m",
                unit=self._unit,
                origin=Origin.ARCHIVE,
                provenance=self._provenance,
            )
        except NormalizationError as exc:
            self._reject(self._archive.first_day, str(exc), fields)
            return
        day = _day(kline.open_ts_us)
        if not self._accept(day, validate_kline(kline), fields):
            return
        previous = self._klines.get(kline.open_ts_us)
        if previous is not None:
            if kline == previous:
                self._duplicates += 1
            else:
                self._reject(day, "candle repetido com conteúdo diferente", fields)
            return
        self._klines[kline.open_ts_us] = kline
        self._write(day, parquet.KLINES_1M, parquet.kline_row(kline))

    def _accept(self, day: date, issues: list[Issue], fields: list[str]) -> bool:
        if day not in self._days:
            self._reject(self._archive.first_day, "fora do período do arquivo", fields)
            return False
        rejecting = [i.value for i in issues if i in REJECTING_ISSUES]
        if rejecting:
            self._reject(day, ",".join(rejecting), fields)
            return False
        return True

    def _write(self, day: date, schema: Any, row: dict[str, Any]) -> None:
        writer = self._writers.get(day)
        if writer is None:
            path = archive_dataset_path(self._data_dir, self._archive.dataset, self._symbol, day)
            writer = parquet.ChunkedParquetWriter(path, schema, ROW_GROUP_ROWS)
            self._writers[day] = writer
        writer.add(row)
        self._rows += 1

    def _reject(self, day: date, reason: str, fields: list[str]) -> None:
        quarantine = self._quarantines.get(day)
        if quarantine is None:
            path = archive_quarantine_path(self._data_dir, self._symbol, self._archive.dataset, day)
            quarantine = QuarantineWriter(path)
            self._quarantines[day] = quarantine
        quarantine.add({"file": self._archive.name, "reason": reason, "line": ",".join(fields)})


def _day(ts_us: int) -> date:
    return _EPOCH + timedelta(days=ts_us // DAY_US)
