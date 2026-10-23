"""Registros canônicos da camada normalizada (doc 06 §5.4)."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

SCHEMA_VERSIONS = {"trades": 1, "klines_1m": 1, "book_top20": 1}


class Origin(Enum):
    LIVE = "live"
    REST = "rest"
    ARCHIVE = "archive"


class Side(Enum):
    BUY = "B"
    SELL = "S"


def aggressor_side(is_buyer_maker: bool) -> Side:
    """Lado agressor (RF-104): comprador maker ⇒ agressor vendedor; caso contrário, comprador."""
    return Side.SELL if is_buyer_maker else Side.BUY


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    trade_id: int
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    trade_ts_us: int
    event_ts_us: int | None
    recv_ts_us: int | None
    is_buyer_maker: bool
    aggressor: Side
    origin: Origin
    provenance: str


@dataclass(frozen=True, slots=True)
class Kline:
    """Candle fechado. Candles em formação nunca viram `Kline` (RF-102)."""

    symbol: str
    interval: str
    open_ts_us: int
    close_ts_us: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_volume: Decimal
    taker_buy_quote_volume: Decimal
    first_trade_id: int | None
    last_trade_id: int | None
    recv_ts_us: int | None
    origin: Origin
    provenance: str


BookLevel = tuple[Decimal, Decimal]
"""(preço, quantidade)."""


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Snapshot parcial do livro. Sem horário da exchange: `recv_ts_us` é a referência (FV-07)."""

    symbol: str
    last_update_id: int
    recv_ts_us: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    provenance: str


@dataclass(frozen=True, slots=True)
class DepthDiffHeader:
    """Cabeçalho de uma mensagem do diferencial, usado só para verificar continuidade."""

    symbol: str
    event_ts_us: int
    first_update_id: int
    final_update_id: int
