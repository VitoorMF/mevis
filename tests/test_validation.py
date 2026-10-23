from dataclasses import replace
from decimal import Decimal

from trader.core.models import BookSnapshot, Kline, Origin, Side, Trade
from trader.core.timeunits import SECOND_US
from trader.quality.validation import (
    REJECTING_ISSUES,
    Issue,
    validate_book,
    validate_kline,
    validate_trade,
)

TS = 1_792_779_042_655_730
D = Decimal

TRADE = Trade(
    symbol="BTCUSDT",
    trade_id=1,
    price=D("81524.01"),
    qty=D("0.00008"),
    quote_qty=D("6.5219208"),
    trade_ts_us=TS,
    event_ts_us=TS,
    recv_ts_us=TS + 300_000,
    is_buyer_maker=False,
    aggressor=Side.BUY,
    origin=Origin.LIVE,
    provenance="s",
)

KLINE = Kline(
    symbol="BTCUSDT",
    interval="1m",
    open_ts_us=1_792_779_000_000_000,
    close_ts_us=1_792_779_059_999_999,
    open=D("81520"),
    high=D("81530.5"),
    low=D("81510.1"),
    close=D("81524.01"),
    volume=D("12.34567"),
    quote_volume=D("1006400.1234567"),
    trade_count=1801,
    taker_buy_volume=D("6"),
    taker_buy_quote_volume=D("489120"),
    first_trade_id=None,
    last_trade_id=None,
    recv_ts_us=None,
    origin=Origin.REST,
    provenance="p",
)

BOOK = BookSnapshot(
    symbol="BTCUSDT",
    last_update_id=1,
    recv_ts_us=TS,
    bids=((D("81524"), D("1.2")), (D("81523.99"), D("0.5"))),
    asks=((D("81524.01"), D("0.3")), (D("81524.02"), D("2"))),
    provenance="s",
)


def test_valid_records_have_no_issues() -> None:
    assert validate_trade(TRADE) == []
    assert validate_kline(KLINE) == []
    assert validate_book(BOOK) == []


def test_non_positive_trade_is_rejected() -> None:
    issues = validate_trade(replace(TRADE, price=D("0"), qty=D("-1")))
    assert issues == [Issue.NON_POSITIVE_PRICE, Issue.NON_POSITIVE_QTY]
    assert set(issues) <= REJECTING_ISSUES


def test_late_trade_accounts_for_clock_offset() -> None:
    late = replace(TRADE, recv_ts_us=TS + 6 * SECOND_US)
    assert validate_trade(late) == [Issue.TRADE_LATE]
    # Relógio local 2 s atrasado: o atraso real é de 4 s, dentro do limite.
    shifted = replace(TRADE, recv_ts_us=TS + 4 * SECOND_US - 2 * SECOND_US)
    assert validate_trade(shifted, clock_offset_us=2 * SECOND_US) == []
    assert Issue.TRADE_LATE not in REJECTING_ISSUES


def test_inconsistent_ohlc() -> None:
    assert validate_kline(replace(KLINE, high=D("81515"))) == [Issue.OHLC_INCONSISTENT]


def test_crossed_book_is_flagged_but_not_rejected() -> None:
    crossed = replace(BOOK, bids=((D("81524.05"), D("1")),))
    assert validate_book(crossed) == [Issue.BOOK_CROSSED]
    assert Issue.BOOK_CROSSED not in REJECTING_ISSUES


def test_unsorted_book_is_rejected() -> None:
    unsorted = replace(BOOK, asks=tuple(reversed(BOOK.asks)))
    assert Issue.BOOK_UNSORTED in validate_book(unsorted)


def test_empty_side() -> None:
    assert validate_book(replace(BOOK, asks=())) == [Issue.BOOK_EMPTY_SIDE]
