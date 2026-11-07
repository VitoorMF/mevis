"""Montagem da série de minutos das features (doc 03 §5.1; RF-203 a RF-205, RF-212).

Lê, para cada dia do período, os candles (`normalized/klines_1m`), `derived/minute_trades`,
`derived/minute_book` e, para o status dos candles ao vivo, `derived/quality_mask`, de um
diretório local ou do S3 com cache local. Em cada canal, um dia com `archive.parquet` usa o
arquivo; os outros, as horas ao vivo. O resultado tem uma linha por minuto UTC do período, com os
valores ainda em DECIMAL, o status válido e o horário de disponibilidade de cada canal (esquema
`trader.features.series.MINUTES`). Minuto sem dado fica nulo e inválido, sem preenchimento
(RN-008). Os primeiros 1.500 minutos do período não têm histórico para as janelas longas: a
montagem não lê dias antes do início, para nunca encostar no holdout sem querer.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.core.holdout import refuse_holdout
from trader.core.timeunits import MINUTE_US
from trader.features.series import ARCHIVE_LATENCY_US, BOOK_FIELDS, MINUTES, VALID_STATUS
from trader.storage.objectstore import LocalDirectoryStore
from trader.storage.rawfiles import file_sha256
from trader.storage.s3cache import S3Cache

DAY_MINUTES = 1440
_EPOCH = date(1970, 1, 1)
CANDLES = "normalized/klines_1m"
TRADES = "derived/minute_trades"
BOOK = "derived/minute_book"
MASK = "derived/quality_mask"
ARCHIVE = "archive"
INVALID_MINUTES_US = (int(datetime(2026, 10, 23, 22, 44, tzinfo=UTC).timestamp()) * 1_000_000,)
"""Minutos inválidos nos negócios e no livro por decisão registrada (doc 06 §8.2)."""

_KEY = re.compile(r"/date=(\d{4}-\d{2}-\d{2})/(archive|\d{2})\.parquet$")
_CANDLE_COLUMNS = ["open_ts_us", "high", "low", "close", "volume", "trade_count", "recv_ts_us"]
_TRADE_COLUMNS = [
    "minute_ts_us",
    "volume",
    "buy_volume",
    "quote_volume",
    "close",
    "size_bins_count",
    "size_bins_volume",
    "last_recv_ts_us",
    "status",
]
_BOOK_COLUMNS = ["minute_ts_us", *BOOK_FIELDS, "status"]


class Source(Protocol):
    """De onde vêm os arquivos: diretório local ou S3 com cache."""

    def keys(self, prefix: str) -> list[str]: ...

    def path(self, key: str) -> Path: ...


class LocalSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def keys(self, prefix: str) -> list[str]:
        return LocalDirectoryStore(self.root).list_keys(prefix)

    def path(self, key: str) -> Path:
        return self.root / key


class CachedS3Source:
    """Lista no S3 e baixa para o cache só o que mudou desde a última vez (pelo ETag)."""

    def __init__(self, cache: S3Cache) -> None:
        self.cache = cache
        self._listed: dict[str, dict[str, Any]] = {}

    def keys(self, prefix: str) -> list[str]:
        objects = self.cache.list(prefix)
        self._listed.update((o["Key"], o) for o in objects)
        return sorted(o["Key"] for o in objects)

    def path(self, key: str) -> Path:
        return self.cache.get(key, self._listed.get(key))


@dataclass
class Minutes:
    table: pa.Table
    """Esquema `MINUTES`: uma linha por minuto de `[start, end]`."""
    inputs: list[dict[str, str]]
    """Arquivos lidos, com o SHA-256 (doc 04 §11)."""
    holdout_days: int
    allow_holdout: bool
    archive_days: dict[str, int]
    live_hours: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def day_start_us(day: date) -> int:
    return (day - _EPOCH).days * DAY_MINUTES * MINUTE_US


def assemble_minutes(
    source: Source,
    *,
    symbol: str,
    kline_channel: str,
    start: date,
    end: date,
    allow_holdout: bool = False,
) -> Minutes:
    """Série de minutos de `[start, end]`; recusa o holdout da trilha A sem `allow_holdout`."""
    if start > end:
        raise ValueError(f"início {start} depois do fim {end}")
    reserved = refuse_holdout(start, end, allow=allow_holdout, requirement="RF-204")
    days = (end - start).days + 1
    first_us = day_start_us(start)
    n = days * DAY_MINUTES
    minutes = first_us + np.arange(n, dtype=np.int64) * MINUTE_US
    reader = _Reader(source, symbol, start, end)

    candles = reader.read(CANDLES, _CANDLE_COLUMNS, archive_allowed=True)
    candles = candles.rename_columns(["minute_ts_us", *_CANDLE_COLUMNS[1:], ARCHIVE])
    mask = reader.read(MASK, ["channel", "minute_ts_us", "status"], archive_allowed=False)
    mask = mask.filter(pc.equal(mask["channel"], kline_channel))
    trades = reader.read(TRADES, _TRADE_COLUMNS, archive_allowed=True)
    book = reader.read(BOOK, _BOOK_COLUMNS, archive_allowed=False)

    columns: dict[str, pa.Array | np.ndarray] = {"minute_ts_us": minutes}
    columns |= _candle_columns(candles, mask, first_us, minutes)
    columns |= _trade_columns(trades, first_us, minutes)
    columns |= _book_columns(book, first_us, minutes)
    for ts in INVALID_MINUTES_US:
        if first_us <= ts < first_us + n * MINUTE_US:
            index = (ts - first_us) // MINUTE_US
            for channel in ("trades", "book"):
                valid = np.asarray(columns[f"{channel}_valid"], dtype=bool)
                valid[index] = False
                columns[f"{channel}_valid"] = valid
    table = pa.table({name: columns[name] for name in MINUTES.names}, schema=MINUTES)
    return Minutes(
        table=table,
        inputs=reader.inputs,
        holdout_days=len(reserved),
        allow_holdout=allow_holdout,
        archive_days=reader.archive_days,
        live_hours=reader.live_hours,
        warnings=reader.warnings,
    )


class _Reader:
    def __init__(self, source: Source, symbol: str, start: date, end: date) -> None:
        self.source = source
        self.symbol = symbol
        self.start = start
        self.end = end
        self.inputs: list[dict[str, str]] = []
        self.archive_days: dict[str, int] = {}
        self.live_hours: dict[str, int] = {}
        self.warnings: list[str] = []

    def read(self, dataset: str, columns: list[str], *, archive_allowed: bool) -> pa.Table:
        """As linhas do período; com `archive_allowed`, a coluna `archive` diz de onde vieram."""
        by_day: dict[date, dict[str, str]] = {}
        for key in self.source.keys(f"{dataset}/symbol={self.symbol}/"):
            match = _KEY.search(key)
            if match is None:
                continue
            day = date.fromisoformat(match[1])
            if self.start <= day <= self.end:
                by_day.setdefault(day, {})[match[2]] = key
        name = dataset.rsplit("/", 1)[1]
        tables = []
        archive_days = live_hours = 0
        for day in sorted(by_day):
            files = by_day[day]
            if archive_allowed and ARCHIVE in files:
                chosen = [(files[ARCHIVE], True)]
                archive_days += 1
            else:
                chosen = [(files[h], False) for h in sorted(files) if h != ARCHIVE]
                live_hours += len(chosen)
            for key, archive in chosen:
                path = self.source.path(key)
                self.inputs.append({"key": key, "sha256": file_sha256(path)})
                table = pq.read_table(path, columns=columns)
                if archive_allowed:
                    table = table.append_column(ARCHIVE, pa.array([archive] * table.num_rows))
                tables.append(table)
        self.archive_days[name] = archive_days
        self.live_hours[name] = live_hours
        missing = (self.end - self.start).days + 1 - len(by_day)
        if missing and archive_allowed:  # o livro e a máscara só existem nos dias ao vivo
            self.warnings.append(f"{name}: {missing} dia(s) do período sem arquivo")
        if not tables:
            return _empty(columns, archive_allowed)
        return pa.concat_tables(tables)


_EMPTY_TYPES: dict[str, pa.DataType] = {
    "channel": pa.string(),
    "status": pa.string(),
}


def _empty(columns: list[str], archive_allowed: bool) -> pa.Table:
    """Tabela sem linhas com os tipos do esquema `MINUTES` (canal sem nenhum arquivo no período)."""
    types: dict[str, pa.DataType] = {}
    for column in columns:
        if column in _EMPTY_TYPES:
            types[column] = _EMPTY_TYPES[column]
        elif column.endswith("_ts_us") or column == "trade_count":
            types[column] = pa.int64()
        elif column in BOOK_FIELDS:
            types[column] = pa.float64()
        else:
            types[column] = next(
                MINUTES.field(f"{prefix}_{column}").type
                for prefix in ("trades", "candle")
                if f"{prefix}_{column}" in MINUTES.names
            )
    if archive_allowed:
        types[ARCHIVE] = pa.bool_()
    return pa.table({k: pa.array([], t) for k, t in types.items()})


@dataclass(frozen=True)
class _Placement:
    """Onde está, em `table`, a linha de cada minuto do período."""

    rows: pa.Array
    """Índice da linha por minuto; nulo onde não há linha."""
    identity: bool
    """A tabela já tem uma linha por minuto, em ordem: dispensa a cópia (`take`)."""

    @property
    def present(self) -> np.ndarray:
        return np.asarray(self.rows.is_valid(), dtype=bool)

    def take(self, column: pa.ChunkedArray) -> pa.ChunkedArray | pa.Array:
        return column if self.identity else column.take(self.rows)


def _place(table: pa.Table, first_us: int, n: int, what: str) -> _Placement:
    ts = np.asarray(table["minute_ts_us"], dtype=np.int64)
    offset = ts - first_us
    if np.any(offset % MINUTE_US):
        raise ValueError(f"{what}: minuto que não começa no segundo zero")
    index = offset // MINUTE_US
    inside = (index >= 0) & (index < n)
    rows, index = np.flatnonzero(inside), index[inside]
    if np.unique(index).size != index.size:
        raise ValueError(f"{what}: minuto repetido")
    positions = np.full(n, -1, dtype=np.int64)
    positions[index] = rows
    identity = len(ts) == n and bool(np.array_equal(index, np.arange(n)))
    return _Placement(pa.array(positions, mask=positions < 0), identity)


def _available(
    received: pa.ChunkedArray | pa.Array, present: np.ndarray, minutes: np.ndarray
) -> pa.Array:
    """Recebimento, ou fim do minuto + L sem ele (doc 04 §5.3); nulo onde não há dado."""
    fallback = minutes + MINUTE_US + ARCHIVE_LATENCY_US
    values = np.asarray(received.fill_null(-1), dtype=np.int64)
    values = np.where(values < 0, fallback, values)
    return pa.array(values, mask=~present)


def _status_valid(status: pa.ChunkedArray | pa.Array) -> np.ndarray:
    return np.asarray(pc.is_in(status, pa.array(sorted(VALID_STATUS))).fill_null(False), bool)


def _candle_columns(
    candles: pa.Table, mask: pa.Table, first_us: int, minutes: np.ndarray
) -> dict[str, Any]:
    n = len(minutes)
    place = _place(candles, first_us, n, CANDLES)
    present = place.present
    archive = np.asarray(place.take(candles[ARCHIVE]).fill_null(False), dtype=bool)
    status = _place(mask, first_us, n, MASK).take(mask["status"])
    out: dict[str, Any] = {
        f"candle_{name}": place.take(candles[name])
        for name in ("high", "low", "close", "volume", "trade_count")
    }
    out["candle_valid"] = present & (archive | _status_valid(status))
    received = place.take(candles["recv_ts_us"])
    out["candle_available_us"] = _available(received, present, minutes)
    return out


def _trade_columns(trades: pa.Table, first_us: int, minutes: np.ndarray) -> dict[str, Any]:
    place = _place(trades, first_us, len(minutes), TRADES)
    present = place.present
    out: dict[str, Any] = {
        f"trades_{name}": place.take(trades[name])
        for name in (
            "volume",
            "buy_volume",
            "quote_volume",
            "close",
            "size_bins_count",
            "size_bins_volume",
        )
    }
    out["trades_valid"] = present & _status_valid(place.take(trades["status"]))
    received = place.take(trades["last_recv_ts_us"])
    out["trades_available_us"] = _available(received, present, minutes)
    return out


def _book_columns(book: pa.Table, first_us: int, minutes: np.ndarray) -> dict[str, Any]:
    place = _place(book, first_us, len(minutes), BOOK)
    present = place.present
    out: dict[str, Any] = {f"book_{name}": place.take(book[name]) for name in BOOK_FIELDS}
    out["book_valid"] = present & _status_valid(place.take(book["status"]))
    out["book_available_us"] = pa.array(minutes + MINUTE_US, mask=~present)
    return out
