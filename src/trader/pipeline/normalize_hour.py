"""Normalização de uma hora (doc 06 §5.4 e §8; RF-128, RF-132).

Lê os arquivos brutos da hora H e as margens das horas vizinhas, em ordem global de
recebimento (merge entre sessões), e processa em fluxo:

- **deduplicação** pela chave do dataset: vale a primeira cópia recebida; as demais são
  descartadas, e uma cópia com conteúdo diferente vai para a quarentena e marca o minuto
  como inválido;
- **partição** pelo horário do evento (negócio, abertura do candle) ou, no livro, pelo
  horário de recebimento da primeira cópia;
- **validação** (RF-155): problemas que recusam o registro mandam-no para a quarentena;
  os demais só marcam o minuto.

A memória fica limitada: só as chaves já vistas (com um hash do conteúdo) ficam em memória,
e os datasets são gravados em blocos.
"""

import heapq
import json
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trader.core.rawformat import RawRecord
from trader.core.timeunits import SECOND_US, TimeUnit, floor_minute
from trader.normalize.binance import (
    NormalizationError,
    parse_ws_depth_diff_header,
    parse_ws_depth_snapshot,
    parse_ws_kline,
    parse_ws_trade,
    symbol_from_stream,
    unwrap_combined,
)
from trader.ops.config import PipelineConfig
from trader.quality.mask import ClockReference, Flag
from trader.quality.validation import (
    REJECTING_ISSUES,
    Issue,
    validate_book,
    validate_kline,
    validate_trade,
)
from trader.storage import parquet
from trader.storage.layout import HOUR_US, RawFile, dataset_path, quarantine_path
from trader.storage.quarantine import QuarantineWriter
from trader.storage.rawfiles import BadLine, iter_records

UNIT = TimeUnit.MICROSECOND


@dataclass
class HourResult:
    hour: int
    rows: Counter[str] = field(default_factory=Counter)
    duplicates: Counter[str] = field(default_factory=Counter)
    quarantined: Counter[str] = field(default_factory=Counter)
    flags: dict[tuple[str, int], set[Flag]] = field(default_factory=dict)
    outputs: list[Path] = field(default_factory=list)
    """Arquivos gravados nesta execução (Parquet e quarentena)."""
    removed: list[Path] = field(default_factory=list)
    """Arquivos de uma normalização anterior que deixaram de existir (hora sem registros)."""


class _HourNormalizer:
    def __init__(self, config: PipelineConfig, hour: int, clock: ClockReference) -> None:
        self.config = config
        self.hour = hour
        self.start_us = hour * HOUR_US
        self.end_us = self.start_us + HOUR_US
        self.clock = clock
        self.result = HourResult(hour)
        trade_stream, kline_stream, top20_stream, diff_stream = config.streams
        self.streams = {
            trade_stream: self._trade,
            kline_stream: self._kline,
            top20_stream: self._top20,
            diff_stream: self._diff,
        }
        self.trade_stream, self.kline_stream = trade_stream, kline_stream
        self.top20_stream, self.diff_stream = top20_stream, diff_stream
        self._seen: dict[str, dict[int, int]] = {s: {} for s in self.streams}
        self._max_update_id: int | None = None
        data_dir, symbol = config.data_dir, config.symbol
        self.writers = {
            "trades": parquet.ChunkedParquetWriter(
                dataset_path(data_dir, "normalized", "trades", symbol, hour),
                parquet.TRADES,
                config.trades_row_group,
            ),
            "klines_1m": parquet.ChunkedParquetWriter(
                dataset_path(data_dir, "normalized", "klines_1m", symbol, hour),
                parquet.KLINES_1M,
                1_000,
            ),
            "book_top20": parquet.ChunkedParquetWriter(
                dataset_path(data_dir, "normalized", "book_top20", symbol, hour),
                parquet.BOOK_TOP20,
                config.books_row_group,
            ),
        }
        self.quarantine = QuarantineWriter(quarantine_path(data_dir, symbol, hour))

    # --------------------------------------------------------------------- entrada

    def run(self, files: Sequence[RawFile]) -> HourResult:
        try:
            for record in heapq.merge(*(self._records(i, f) for i, f in enumerate(files))):
                self._handle(record[3])
        except BaseException:
            for writer in self.writers.values():
                writer.abort()
            self.quarantine.abort()
            raise
        for dataset, writer in self.writers.items():
            if writer.close():
                self.result.outputs.append(writer.path)
            else:
                self.result.removed.append(writer.path)
            self.result.rows[dataset] = writer.rows_written
        if self.quarantine.close():
            self.result.outputs.append(self.quarantine.path)
        else:
            self.result.removed.append(self.quarantine.path)
        return self.result

    def _records(self, index: int, raw_file: RawFile) -> Iterator[tuple[int, int, int, RawRecord]]:
        margin = int(self.config.boundary_margin_s * SECOND_US)
        low, high = self.start_us - margin, self.end_us + margin
        for line_no, item in enumerate(iter_records(raw_file.path)):
            if isinstance(item, BadLine):
                if raw_file.hour == self.hour:
                    self._reject(raw_file.channel_dir, item.reason, raw=item.line)
                continue
            if raw_file.hour == self.hour or low <= item.recv_ts_us < high:
                yield item.recv_ts_us, index, line_no, item

    def _handle(self, record: RawRecord) -> None:
        handler = self.streams.get(record.channel)
        if handler is None:
            return
        try:
            stream, data = unwrap_combined(json.loads(record.payload))
            if stream != record.channel:
                raise NormalizationError(f"stream {stream!r} em arquivo de {record.channel!r}")
            handler(record, data)
        except (ValueError, NormalizationError) as exc:
            if self._in_hour(record.recv_ts_us):
                self._reject(record.channel, str(exc), record=record)
                self._flag(record.channel, record.recv_ts_us, Flag.INVALID)

    # --------------------------------------------------------------------- canais

    def _trade(self, record: RawRecord, data: Mapping[str, Any]) -> None:
        trade = parse_ws_trade(
            data, recv_ts_us=record.recv_ts_us, unit=UNIT, provenance=record.session
        )
        fingerprint = hash(
            (str(trade.price), str(trade.qty), trade.trade_ts_us, trade.is_buyer_maker)
        )
        if not self._first_copy(record, trade.trade_id, fingerprint, trade.trade_ts_us):
            return
        if not self._in_hour(trade.trade_ts_us):
            return
        offset = self.clock.offset_at(record.recv_ts_us) or 0
        issues = validate_trade(
            trade,
            clock_offset_us=offset,
            late_limit_us=int(self.config.late_limit_s * SECOND_US),
        )
        if not self._accept(record, issues, trade.trade_ts_us):
            return
        if Issue.TRADE_LATE in issues:
            self._flag(record.channel, trade.trade_ts_us, Flag.LATE)
        self._flag(record.channel, trade.trade_ts_us, Flag.DATA)
        self.writers["trades"].add(parquet.trade_row(trade))

    def _kline(self, record: RawRecord, data: Mapping[str, Any]) -> None:
        kline = parse_ws_kline(
            data, recv_ts_us=record.recv_ts_us, unit=UNIT, provenance=record.session
        )
        if kline is None:
            return
        fingerprint = hash(
            (
                str(kline.open),
                str(kline.high),
                str(kline.low),
                str(kline.close),
                str(kline.volume),
                kline.trade_count,
            )
        )
        if not self._first_copy(record, kline.open_ts_us, fingerprint, kline.open_ts_us):
            return
        if not self._in_hour(kline.open_ts_us):
            return
        if not self._accept(record, validate_kline(kline), kline.open_ts_us):
            return
        self._flag(record.channel, kline.open_ts_us, Flag.DATA)
        self.writers["klines_1m"].add(parquet.kline_row(kline))

    def _top20(self, record: RawRecord, data: Mapping[str, Any]) -> None:
        book = parse_ws_depth_snapshot(
            data,
            symbol=symbol_from_stream(record.channel),
            recv_ts_us=record.recv_ts_us,
            provenance=record.session,
        )
        fingerprint = hash((tuple(map(str, book.bids)), tuple(map(str, book.asks))))
        if not self._first_copy(record, book.last_update_id, fingerprint, record.recv_ts_us):
            return
        regressed = self._max_update_id is not None and book.last_update_id < self._max_update_id
        self._max_update_id = max(self._max_update_id or 0, book.last_update_id)
        if not self._in_hour(record.recv_ts_us):
            return
        issues = validate_book(book)
        if not self._accept(record, issues, record.recv_ts_us):
            return
        if issues or regressed:
            self._flag(record.channel, record.recv_ts_us, Flag.INVALID)
        self._flag(record.channel, record.recv_ts_us, Flag.DATA)
        self.writers["book_top20"].add(parquet.book_row(book))

    def _diff(self, record: RawRecord, data: Mapping[str, Any]) -> None:
        """O diferencial fica só na camada bruta; aqui ele só alimenta a máscara."""
        header = parse_ws_depth_diff_header(data, unit=UNIT)
        if self._in_hour(header.event_ts_us):
            self._flag(record.channel, header.event_ts_us, Flag.DATA)

    # --------------------------------------------------------------------- apoio

    def _first_copy(self, record: RawRecord, key: int, fingerprint: int, ts_us: int) -> bool:
        seen = self._seen[record.channel]
        previous = seen.get(key)
        if previous is None:
            seen[key] = fingerprint
            return True
        if self._in_hour(ts_us):
            if previous == fingerprint:
                self.result.duplicates[record.channel] += 1
            else:
                self._reject(record.channel, "cópia com conteúdo diferente", record=record)
                self._flag(record.channel, ts_us, Flag.INVALID)
        return False

    def _accept(self, record: RawRecord, issues: Sequence[Issue], ts_us: int) -> bool:
        rejecting = [i.value for i in issues if i in REJECTING_ISSUES]
        if not rejecting:
            return True
        self._reject(record.channel, ",".join(rejecting), record=record)
        self._flag(record.channel, ts_us, Flag.INVALID)
        return False

    def _in_hour(self, ts_us: int) -> bool:
        return self.start_us <= ts_us < self.end_us

    def _flag(self, channel: str, ts_us: int, flag: Flag) -> None:
        if self._in_hour(ts_us):
            self.result.flags.setdefault((channel, floor_minute(ts_us)), set()).add(flag)

    def _reject(
        self,
        channel: str,
        reason: str,
        *,
        record: RawRecord | None = None,
        raw: bytes | None = None,
    ) -> None:
        entry: dict[str, Any] = {"channel": channel, "reason": reason}
        if record is not None:
            entry |= {
                "session": record.session,
                "seq": record.seq,
                "recv_ts_us": record.recv_ts_us,
                "payload": record.payload.decode(errors="replace"),
            }
        if raw is not None:
            entry["line"] = raw.decode(errors="replace")
        self.quarantine.add(entry)
        self.result.quarantined[channel] += 1


def normalize_hour(
    config: PipelineConfig, hour: int, files: Sequence[RawFile], clock: ClockReference
) -> HourResult:
    """Normaliza a hora `hour` a partir de `files` (horas H-1, H e H+1, já fechados)."""
    ordered = sorted(files, key=lambda f: str(f.path))
    return _HourNormalizer(config, hour, clock).run(ordered)
