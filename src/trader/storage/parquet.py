"""Esquemas e escrita em blocos dos datasets Parquet (doc 06 §5.3 e §5.4).

A escrita é em blocos (`row_group_rows`) para manter a memória limitada (doc 03 §6.3), e
atômica: o arquivo só aparece no caminho final depois de completo.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from trader.core.models import SCHEMA_VERSIONS, BookSnapshot, Kline, Trade
from trader.storage.layout import replace_atomically

PRICE = pa.decimal128(18, 8)
AMOUNT = pa.decimal128(38, 8)

_COMMON = [
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("origin", pa.string(), nullable=False),
    pa.field("provenance", pa.string(), nullable=False),
    pa.field("schema_version", pa.int16(), nullable=False),
]

TRADES = pa.schema(
    [
        pa.field("trade_id", pa.int64(), nullable=False),
        pa.field("price", PRICE, nullable=False),
        pa.field("qty", PRICE, nullable=False),
        pa.field("quote_qty", AMOUNT, nullable=False),
        pa.field("trade_ts_us", pa.int64(), nullable=False),
        pa.field("event_ts_us", pa.int64()),
        pa.field("recv_ts_us", pa.int64()),
        pa.field("is_buyer_maker", pa.bool_(), nullable=False),
        pa.field("aggressor", pa.string(), nullable=False),
        *_COMMON,
    ]
)

KLINES_1M = pa.schema(
    [
        pa.field("open_ts_us", pa.int64(), nullable=False),
        pa.field("close_ts_us", pa.int64(), nullable=False),
        pa.field("open", PRICE, nullable=False),
        pa.field("high", PRICE, nullable=False),
        pa.field("low", PRICE, nullable=False),
        pa.field("close", PRICE, nullable=False),
        pa.field("volume", AMOUNT, nullable=False),
        pa.field("quote_volume", AMOUNT, nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
        pa.field("taker_buy_volume", AMOUNT, nullable=False),
        pa.field("taker_buy_quote_volume", AMOUNT, nullable=False),
        pa.field("first_trade_id", pa.int64()),
        pa.field("last_trade_id", pa.int64()),
        pa.field("recv_ts_us", pa.int64()),
        *_COMMON,
    ]
)

BOOK_TOP20 = pa.schema(
    [
        pa.field("last_update_id", pa.int64(), nullable=False),
        pa.field("recv_ts_us", pa.int64(), nullable=False),
        pa.field("bid_px", pa.list_(PRICE), nullable=False),
        pa.field("bid_qty", pa.list_(PRICE), nullable=False),
        pa.field("ask_px", pa.list_(PRICE), nullable=False),
        pa.field("ask_qty", pa.list_(PRICE), nullable=False),
        *_COMMON,
    ]
)

QUALITY_MASK = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("channel", pa.string(), nullable=False),
        pa.field("minute_ts_us", pa.int64(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("details", pa.string(), nullable=False),
        pa.field("schema_version", pa.int16(), nullable=False),
    ]
)


def trade_row(trade: Trade) -> dict[str, Any]:
    return {
        "trade_id": trade.trade_id,
        "price": trade.price,
        "qty": trade.qty,
        "quote_qty": trade.quote_qty,
        "trade_ts_us": trade.trade_ts_us,
        "event_ts_us": trade.event_ts_us,
        "recv_ts_us": trade.recv_ts_us,
        "is_buyer_maker": trade.is_buyer_maker,
        "aggressor": trade.aggressor.value,
        "symbol": trade.symbol,
        "origin": trade.origin.value,
        "provenance": trade.provenance,
        "schema_version": SCHEMA_VERSIONS["trades"],
    }


def kline_row(kline: Kline) -> dict[str, Any]:
    return {
        "open_ts_us": kline.open_ts_us,
        "close_ts_us": kline.close_ts_us,
        "open": kline.open,
        "high": kline.high,
        "low": kline.low,
        "close": kline.close,
        "volume": kline.volume,
        "quote_volume": kline.quote_volume,
        "trade_count": kline.trade_count,
        "taker_buy_volume": kline.taker_buy_volume,
        "taker_buy_quote_volume": kline.taker_buy_quote_volume,
        "first_trade_id": kline.first_trade_id,
        "last_trade_id": kline.last_trade_id,
        "recv_ts_us": kline.recv_ts_us,
        "symbol": kline.symbol,
        "origin": kline.origin.value,
        "provenance": kline.provenance,
        "schema_version": SCHEMA_VERSIONS["klines_1m"],
    }


def book_row(book: BookSnapshot) -> dict[str, Any]:
    return {
        "last_update_id": book.last_update_id,
        "recv_ts_us": book.recv_ts_us,
        "bid_px": [price for price, _ in book.bids],
        "bid_qty": [qty for _, qty in book.bids],
        "ask_px": [price for price, _ in book.asks],
        "ask_qty": [qty for _, qty in book.asks],
        "symbol": book.symbol,
        "origin": "live",
        "provenance": book.provenance,
        "schema_version": SCHEMA_VERSIONS["book_top20"],
    }


class ChunkedParquetWriter:
    """Acumula linhas e grava um row group a cada `row_group_rows`."""

    def __init__(self, path: Path, schema: Any, row_group_rows: int) -> None:
        self.path = path
        self.rows_written = 0
        self._schema = schema
        self._limit = row_group_rows
        self._tmp = path.with_name(path.name + ".tmp")
        self._buffer: list[Mapping[str, Any]] = []
        self._writer: Any = None

    def add(self, row: Mapping[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._limit:
            self._flush()

    def close(self) -> bool:
        """Finaliza o arquivo. Devolve False (e não cria nada) se não houve nenhuma linha."""
        self._flush()
        if self._writer is None:
            self.path.unlink(missing_ok=True)
            return False
        self._writer.close()
        replace_atomically(self._tmp, self.path)
        return True

    def abort(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._tmp.unlink(missing_ok=True)

    def _flush(self) -> None:
        if not self._buffer:
            return
        if self._writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = pq.ParquetWriter(self._tmp, self._schema, compression="zstd")
        table = pa.Table.from_pylist(self._buffer, schema=self._schema)
        self._writer.write_table(table)
        self.rows_written += len(self._buffer)
        self._buffer.clear()


def write_rows(path: Path, schema: Any, rows: Sequence[Mapping[str, Any]]) -> bool:
    writer = ChunkedParquetWriter(path, schema, max(len(rows), 1))
    for row in rows:
        writer.add(row)
    return writer.close()


def read_table(path: Path) -> Any:
    return pq.read_table(path)
