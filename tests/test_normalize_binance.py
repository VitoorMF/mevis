from decimal import Decimal
from typing import Any

import pytest

from trader.core.models import Origin, Side
from trader.core.timeunits import TimeUnit
from trader.normalize.binance import (
    NormalizationError,
    parse_archive_trade,
    parse_rest_kline,
    parse_rest_trade,
    parse_ws_depth_diff_header,
    parse_ws_depth_snapshot,
    parse_ws_kline,
    parse_ws_trade,
    symbol_from_stream,
    unwrap_combined,
)

RECV = 1_792_779_042_700_000
US = TimeUnit.MICROSECOND
MS = TimeUnit.MILLISECOND


def ws_trade(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "e": "trade",
        "E": 1_792_779_042_655_963,
        "s": "BTCUSDT",
        "t": 6_787_400_213,
        "p": "81524.01000000",
        "q": "0.00008000",
        "T": 1_792_779_042_655_730,
        "m": False,
        "M": True,
    }
    data.update(overrides)
    return data


def ws_kline(*, closed: bool, first_id: int = 6_787_400_100) -> dict[str, Any]:
    return {
        "e": "kline",
        "E": 1_792_779_060_001_000,
        "s": "BTCUSDT",
        "k": {
            "t": 1_792_779_000_000_000,
            "T": 1_792_779_059_999_999,
            "s": "BTCUSDT",
            "i": "1m",
            "f": first_id,
            "L": 6_787_401_900 if first_id >= 0 else -1,
            "o": "81520.00000000",
            "c": "81524.01000000",
            "h": "81530.50000000",
            "l": "81510.10000000",
            "v": "12.34567000",
            "n": 1801,
            "x": closed,
            "q": "1006400.12345670",
            "V": "6.00000000",
            "Q": "489120.00000000",
            "B": "0",
        },
    }


KLINE_ROW_MS: list[Any] = [
    1_729_706_400_000,
    "65723.31000000",
    "65800.00000000",
    "65700.00000000",
    "65750.00000000",
    "10.50000000",
    1_729_706_459_999,
    "690000.00000000",
    1500,
    "5.00000000",
    "328000.00000000",
    "0",
]


class TestWebSocketTrade:
    def test_fields(self) -> None:
        trade = parse_ws_trade(ws_trade(), recv_ts_us=RECV, unit=US, provenance="sess-1")
        assert trade.trade_id == 6_787_400_213
        assert trade.price == Decimal("81524.01")
        assert trade.quote_qty == Decimal("6.5219208")
        assert trade.trade_ts_us == 1_792_779_042_655_730
        assert trade.event_ts_us == 1_792_779_042_655_963
        assert trade.recv_ts_us == RECV
        assert trade.origin is Origin.LIVE
        assert trade.provenance == "sess-1"

    def test_buyer_taker_is_buy_aggressor(self) -> None:
        trade = parse_ws_trade(ws_trade(m=False), recv_ts_us=RECV, unit=US, provenance="s")
        assert trade.aggressor is Side.BUY

    def test_buyer_maker_is_sell_aggressor(self) -> None:
        trade = parse_ws_trade(ws_trade(m=True), recv_ts_us=RECV, unit=US, provenance="s")
        assert trade.aggressor is Side.SELL

    def test_millisecond_payload(self) -> None:
        payload = ws_trade(E=1_792_779_042_655, T=1_792_779_042_655)
        trade = parse_ws_trade(payload, recv_ts_us=RECV, unit=MS, provenance="s")
        assert trade.trade_ts_us == 1_792_779_042_655_000

    @pytest.mark.parametrize(
        "payload",
        [
            ws_trade(e="aggTrade"),
            {k: v for k, v in ws_trade().items() if k != "p"},
            ws_trade(p="abc"),
            ws_trade(m="maybe"),
            ws_trade(t=True),
        ],
    )
    def test_malformed_payloads_raise_normalization_error(self, payload: dict[str, Any]) -> None:
        with pytest.raises(NormalizationError):
            parse_ws_trade(payload, recv_ts_us=RECV, unit=US, provenance="s")

    def test_declared_unit_mismatch_is_rejected(self) -> None:
        with pytest.raises(NormalizationError):
            parse_ws_trade(ws_trade(), recv_ts_us=RECV, unit=MS, provenance="s")


class TestHistoricalTrades:
    def test_rest_row(self) -> None:
        row = {
            "id": 6_787_400_213,
            "price": "81524.01000000",
            "qty": "0.00008000",
            "quoteQty": "6.52192080",
            "time": 1_792_779_038_393_277,
            "isBuyerMaker": False,
            "isBestMatch": True,
        }
        trade = parse_rest_trade(row, symbol="BTCUSDT", unit=US, provenance="rec-1")
        assert trade.origin is Origin.REST
        assert trade.recv_ts_us is None
        assert trade.event_ts_us is None
        assert trade.aggressor is Side.BUY

    def test_quote_qty_mismatch_is_rejected(self) -> None:
        row = {
            "id": 1,
            "price": "81524.01000000",
            "qty": "0.00008000",
            "quoteQty": "6.52192081",
            "time": 1_792_779_038_393_277,
            "isBuyerMaker": False,
        }
        with pytest.raises(NormalizationError):
            parse_rest_trade(row, symbol="BTCUSDT", unit=US, provenance="r")

    def test_archive_csv_line_in_milliseconds(self) -> None:
        fields = [
            "3955079989",
            "65723.31000000",
            "0.00031000",
            "20.37422610",
            "1729706400305",
            "True",
            "True",
        ]
        trade = parse_archive_trade(fields, symbol="BTCUSDT", unit=MS, provenance="file.zip")
        assert trade.trade_id == 3_955_079_989
        assert trade.trade_ts_us == 1_729_706_400_305_000
        assert trade.aggressor is Side.SELL
        assert trade.origin is Origin.ARCHIVE

    def test_short_archive_line_is_rejected(self) -> None:
        with pytest.raises(NormalizationError):
            parse_archive_trade(["1", "2"], symbol="BTCUSDT", unit=MS, provenance="f")


class TestKlines:
    def test_open_candle_is_ignored(self) -> None:
        assert (
            parse_ws_kline(ws_kline(closed=False), recv_ts_us=RECV, unit=US, provenance="s") is None
        )

    def test_closed_candle(self) -> None:
        kline = parse_ws_kline(ws_kline(closed=True), recv_ts_us=RECV, unit=US, provenance="s")
        assert kline is not None
        assert kline.open_ts_us == 1_792_779_000_000_000
        assert kline.close == Decimal("81524.01")
        assert kline.trade_count == 1801
        assert kline.first_trade_id == 6_787_400_100
        assert kline.recv_ts_us == RECV

    def test_candle_without_trades_has_no_trade_ids(self) -> None:
        payload = ws_kline(closed=True, first_id=-1)
        kline = parse_ws_kline(payload, recv_ts_us=RECV, unit=US, provenance="s")
        assert kline is not None
        assert kline.first_trade_id is None
        assert kline.last_trade_id is None

    def test_rest_and_archive_rows_produce_the_same_candle(self) -> None:
        rest = parse_rest_kline(
            KLINE_ROW_MS,
            symbol="BTCUSDT",
            interval="1m",
            unit=MS,
            origin=Origin.REST,
            provenance="p",
        )
        archive_row = [str(value) for value in KLINE_ROW_MS]
        archive = parse_rest_kline(
            archive_row,
            symbol="BTCUSDT",
            interval="1m",
            unit=MS,
            origin=Origin.ARCHIVE,
            provenance="p",
        )
        assert rest.open_ts_us == 1_729_706_400_000_000
        assert rest.recv_ts_us is None
        assert (rest.open, rest.close, rest.volume, rest.trade_count) == (
            archive.open,
            archive.close,
            archive.volume,
            archive.trade_count,
        )

    def test_rest_kline_cannot_be_live(self) -> None:
        with pytest.raises(NormalizationError):
            parse_rest_kline(
                KLINE_ROW_MS,
                symbol="BTCUSDT",
                interval="1m",
                unit=MS,
                origin=Origin.LIVE,
                provenance="p",
            )


class TestBook:
    def test_depth_snapshot(self) -> None:
        data = {
            "lastUpdateId": 101_833_930_968,
            "bids": [["81524.00000000", "1.20000000"], ["81523.99000000", "0.50000000"]],
            "asks": [["81524.01000000", "0.30000000"], ["81524.02000000", "2.00000000"]],
        }
        book = parse_ws_depth_snapshot(data, symbol="BTCUSDT", recv_ts_us=RECV, provenance="s")
        assert book.last_update_id == 101_833_930_968
        assert book.bids[0] == (Decimal("81524"), Decimal("1.2"))
        assert len(book.asks) == 2

    def test_malformed_level_is_rejected(self) -> None:
        data = {"lastUpdateId": 1, "bids": [["81524.00000000"]], "asks": []}
        with pytest.raises(NormalizationError):
            parse_ws_depth_snapshot(data, symbol="BTCUSDT", recv_ts_us=RECV, provenance="s")

    def test_diff_header(self) -> None:
        data = {
            "e": "depthUpdate",
            "E": 1_792_779_042_314_440,
            "s": "BTCUSDT",
            "U": 101_833_930_964,
            "u": 101_833_930_968,
            "b": [],
            "a": [],
        }
        header = parse_ws_depth_diff_header(data, unit=US)
        assert (header.first_update_id, header.final_update_id) == (
            101_833_930_964,
            101_833_930_968,
        )


class TestCombinedStreams:
    def test_unwrap(self) -> None:
        stream, data = unwrap_combined({"stream": "btcusdt@trade", "data": {"e": "trade"}})
        assert stream == "btcusdt@trade"
        assert data == {"e": "trade"}

    def test_unwrap_rejects_other_shapes(self) -> None:
        with pytest.raises(NormalizationError):
            unwrap_combined({"result": None, "id": 1})

    def test_symbol_from_stream(self) -> None:
        assert symbol_from_stream("btcusdt@depth20@100ms") == "BTCUSDT"
        with pytest.raises(NormalizationError):
            symbol_from_stream("@trade")
