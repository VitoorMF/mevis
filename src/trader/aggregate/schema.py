"""Esquemas de `minute_trades` e `minute_book` (doc 06 §5.6)."""

import pyarrow as pa

PRICE = pa.decimal128(18, 8)
AMOUNT = pa.decimal128(38, 8)
SCHEMA_VERSION = 1

BIN_MIN = -68
BIN_MAX = 32
BINS = BIN_MAX - BIN_MIN + 1
"""Faixas de tamanho `floor(4 * log2(qty))`, limitadas a [-68, 32]; índice = faixa + 68 (DEC-31)."""

MINUTE_TRADES = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("minute_ts_us", pa.int64(), nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
        pa.field("buy_count", pa.int64(), nullable=False),
        pa.field("volume", AMOUNT, nullable=False),
        pa.field("buy_volume", AMOUNT, nullable=False),
        pa.field("quote_volume", AMOUNT, nullable=False),
        pa.field("buy_quote_volume", AMOUNT, nullable=False),
        pa.field("open", PRICE),
        pa.field("high", PRICE),
        pa.field("low", PRICE),
        pa.field("close", PRICE),
        pa.field("first_trade_id", pa.int64()),
        pa.field("last_trade_id", pa.int64()),
        pa.field("last_recv_ts_us", pa.int64()),
        pa.field("size_bins_count", pa.list_(pa.int32()), nullable=False),
        pa.field("size_bins_volume", pa.list_(AMOUNT), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("schema_version", pa.int32(), nullable=False),
    ]
)

MEASURES = (
    "spread_rel",
    "imbalance_1",
    "imbalance_5",
    "imbalance_10",
    "imbalance_20",
    "microprice_dist",
)
STATS = ("mean", "min", "last")

MINUTE_BOOK = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("minute_ts_us", pa.int64(), nullable=False),
        pa.field("snapshot_count", pa.int32(), nullable=False),
        pa.field("last_recv_ts_us", pa.int64()),
        *[pa.field(f"{m}_{s}", pa.float64()) for m in MEASURES for s in STATS],
        pa.field("status", pa.string(), nullable=False),
        pa.field("schema_version", pa.int32(), nullable=False),
    ]
)
