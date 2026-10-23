"""Verificações de consistência dos registros normalizados (RF-155; doc 06 §8.1)."""

from enum import Enum

from trader.core.models import BookSnapshot, Kline, Trade
from trader.core.timeunits import SECOND_US


class Issue(Enum):
    NON_POSITIVE_PRICE = "non_positive_price"
    NON_POSITIVE_QTY = "non_positive_qty"
    NEGATIVE_VOLUME = "negative_volume"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    TRADE_LATE = "trade_late"
    BOOK_EMPTY_SIDE = "book_empty_side"
    BOOK_UNSORTED = "book_unsorted"
    BOOK_CROSSED = "book_crossed"


REJECTING_ISSUES = frozenset(
    {
        Issue.NON_POSITIVE_PRICE,
        Issue.NON_POSITIVE_QTY,
        Issue.NEGATIVE_VOLUME,
        Issue.OHLC_INCONSISTENT,
        Issue.BOOK_UNSORTED,
    }
)
"""Problemas que mandam o registro para a quarentena; os demais só marcam a máscara."""

DEFAULT_LATE_LIMIT_US = 5 * SECOND_US  # doc 06 §8.1 [PROPOSTA]


def validate_trade(
    trade: Trade, *, clock_offset_us: int = 0, late_limit_us: int = DEFAULT_LATE_LIMIT_US
) -> list[Issue]:
    """`clock_offset_us` = horário do servidor - horário local (doc 06 §4.2)."""
    issues = []
    if trade.price <= 0:
        issues.append(Issue.NON_POSITIVE_PRICE)
    if trade.qty <= 0:
        issues.append(Issue.NON_POSITIVE_QTY)
    if trade.recv_ts_us is not None:
        delay = trade.recv_ts_us + clock_offset_us - trade.trade_ts_us
        if delay > late_limit_us:
            issues.append(Issue.TRADE_LATE)
    return issues


def validate_kline(kline: Kline) -> list[Issue]:
    issues = []
    prices = (kline.open, kline.high, kline.low, kline.close)
    if any(p <= 0 for p in prices):
        issues.append(Issue.NON_POSITIVE_PRICE)
    if not (
        kline.low <= min(kline.open, kline.close) and max(kline.open, kline.close) <= kline.high
    ):
        issues.append(Issue.OHLC_INCONSISTENT)
    volumes = (
        kline.volume,
        kline.quote_volume,
        kline.taker_buy_volume,
        kline.taker_buy_quote_volume,
    )
    if any(v < 0 for v in volumes) or kline.trade_count < 0:
        issues.append(Issue.NEGATIVE_VOLUME)
    return issues


def validate_book(book: BookSnapshot) -> list[Issue]:
    issues = []
    levels = book.bids + book.asks
    if any(price <= 0 for price, _ in levels):
        issues.append(Issue.NON_POSITIVE_PRICE)
    if any(qty <= 0 for _, qty in levels):
        issues.append(Issue.NON_POSITIVE_QTY)
    if not book.bids or not book.asks:
        issues.append(Issue.BOOK_EMPTY_SIDE)
        return issues
    bid_prices = [price for price, _ in book.bids]
    ask_prices = [price for price, _ in book.asks]
    if bid_prices != sorted(bid_prices, reverse=True) or ask_prices != sorted(ask_prices):
        issues.append(Issue.BOOK_UNSORTED)
    if bid_prices[0] >= ask_prices[0]:
        issues.append(Issue.BOOK_CROSSED)
    return issues
