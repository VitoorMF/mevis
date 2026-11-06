"""Agregados por minuto dos negócios (doc 06 §5.6; RF-171 a RF-178)."""

import random
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pyarrow as pa
import pytest

from trader.aggregate.schema import BIN_MIN, BINS, MINUTE_TRADES
from trader.aggregate.trades import (
    MISSING,
    OK,
    THRESHOLDS,
    TradeEdge,
    TradeMinutes,
    boundary_span,
    mark_missing,
    size_bin,
    size_bins,
)
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.storage import parquet

T0 = 1_792_764_000_000_000  # 2026-10-23 14:00 UTC
HOUR_END = T0 + 60 * MINUTE_US


def trade(
    trade_id: int,
    ts: int,
    price: str = "100",
    qty: str = "1",
    *,
    buy: bool = True,
    recv: int | None = None,
) -> dict[str, Any]:
    p, q = Decimal(price), Decimal(qty)
    return {
        "trade_id": trade_id,
        "price": p,
        "qty": q,
        "quote_qty": (p * q).quantize(Decimal("1E-8")),
        "trade_ts_us": ts,
        "event_ts_us": ts,
        "recv_ts_us": ts + 50_000 if recv is None else recv,
        "is_buyer_maker": not buy,
        "aggressor": "B" if buy else "S",
        "symbol": "BTCUSDT",
        "origin": "live",
        "provenance": "S1",
        "schema_version": 1,
    }


def table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=parquet.TRADES)


def aggregate(*batches: list[dict[str, Any]]) -> TradeMinutes:
    minutes = TradeMinutes()
    for rows in batches:
        minutes.add(table(rows))
    return minutes


# ------------------------------------------------------------------ faixas de tamanho (DEC-31)


def test_thresholds_are_exact_fourth_roots() -> None:
    for index, units in enumerate(THRESHOLDS):
        target = Fraction(2) ** (BIN_MIN + index) * 10**32
        assert units**4 >= target
        assert (units - 1) ** 4 < target


@pytest.mark.parametrize(
    ("qty", "band"),
    [
        ("1", 0),
        ("0.5", -4),
        ("2", 4),
        ("1.18920711", 0),  # logo abaixo de 2^(1/4)
        ("1.18920712", 1),
        ("0.00390625", -32),  # 2^-8 exato
        ("0.00001", -67),
        ("0.00000001", -68),  # abaixo do limite inferior: primeira faixa
        ("256", 32),
        ("5000", 32),  # acima do limite superior: última faixa
    ],
)
def test_size_bin(qty: str, band: int) -> None:
    assert size_bin(Decimal(qty)) == band - BIN_MIN


def test_vectorized_bins_match_exact_bins() -> None:
    rng = random.Random(7)
    units = {1, 10**12}
    for threshold in THRESHOLDS:
        units |= {threshold - 1, threshold, threshold + 1}
    units |= {rng.randint(1, 10**11) for _ in range(5_000)}
    units |= {int(10 ** rng.uniform(0, 11)) for _ in range(5_000)}
    quantities = [Decimal(u).scaleb(-8) for u in sorted(units) if 0 < u < 10**18]
    array = pa.array(quantities, parquet.PRICE)
    assert size_bins(array).to_pylist() == [size_bin(q) for q in quantities]


# ------------------------------------------------------------------ agregação


def test_minute_values() -> None:
    minutes = aggregate(
        [
            trade(10, T0 + 1 * SECOND_US, "100", "0.5"),
            trade(11, T0 + 2 * SECOND_US, "105", "0.25", buy=False),
            trade(12, T0 + 3 * SECOND_US, "99", "1.5"),
            trade(13, T0 + 59 * SECOND_US, "101", "0.00008", buy=False),
            trade(14, T0 + 2 * MINUTE_US, "102", "2"),
        ]
    )
    rows = minutes.table("BTCUSDT", T0, HOUR_END, source="live", statuses={T0: OK}).to_pylist()
    assert len(rows) == 60
    first = rows[0]
    assert (first["trade_count"], first["buy_count"]) == (4, 2)
    assert first["volume"] == Decimal("2.25008")
    assert first["buy_volume"] == Decimal("2.0")
    assert first["quote_volume"] == Decimal("50") + Decimal("26.25") + Decimal("148.5") + Decimal(
        "0.00808"
    )
    assert first["buy_quote_volume"] == Decimal("50") + Decimal("148.5")
    assert (first["open"], first["high"], first["low"], first["close"]) == (
        Decimal(100),
        Decimal(105),
        Decimal(99),
        Decimal(101),
    )
    assert (first["first_trade_id"], first["last_trade_id"]) == (10, 13)
    assert first["last_recv_ts_us"] == T0 + 59 * SECOND_US + 50_000
    assert sum(first["size_bins_count"]) == 4
    assert first["size_bins_count"][size_bin(Decimal("0.5"))] == 1
    assert sum(first["size_bins_volume"]) == first["volume"]
    assert first["status"] == OK

    empty = rows[1]
    assert (empty["trade_count"], empty["volume"], empty["open"], empty["first_trade_id"]) == (
        0,
        Decimal(0),
        None,
        None,
    )
    assert empty["size_bins_count"] == [0] * BINS
    assert empty["status"] == MISSING  # sem status informado
    assert rows[2]["trade_count"] == 1


def test_result_does_not_depend_on_order_or_batches() -> None:
    """RF-173: somas exatas e candle pelos ids, em qualquer ordem de lotes e de linhas."""
    rng = random.Random(3)
    rows = [
        trade(
            100 + i,
            T0 + i * 700_000,
            f"{rng.uniform(90, 110):.2f}",
            f"{rng.uniform(0.00001, 3):.8f}",
            buy=rng.random() < 0.5,
        )
        for i in range(2_000)
    ]
    reference = aggregate(rows).table("BTCUSDT", T0, HOUR_END, source="live", statuses={})
    shuffled = rows[:]
    rng.shuffle(shuffled)
    batches = [shuffled[i : i + 137] for i in range(0, len(shuffled), 137)]
    other = aggregate(*batches).table("BTCUSDT", T0, HOUR_END, source="live", statuses={})
    assert other.equals(reference)
    assert reference.schema == MINUTE_TRADES


def test_recovered_trades_without_receive_time() -> None:
    """Negócios recuperados do arquivo não têm `recv_ts_us`; o último recebimento ignora-os."""
    minutes = aggregate(
        [
            trade(1, T0 + 1 * SECOND_US, recv=T0 + 2 * SECOND_US),
            trade(2, T0 + 30 * SECOND_US, recv=None) | {"recv_ts_us": None},
            trade(3, T0 + MINUTE_US + 1, recv=None) | {"recv_ts_us": None},
        ]
    )
    rows = minutes.table("BTCUSDT", T0, HOUR_END, source="live", statuses={}).to_pylist()
    assert rows[0]["last_recv_ts_us"] == T0 + 2 * SECOND_US
    assert rows[1]["last_recv_ts_us"] is None


def test_trade_outside_the_period_is_refused() -> None:
    minutes = aggregate([trade(1, HOUR_END + 1)])
    with pytest.raises(ValueError, match="fora do período"):
        minutes.table("BTCUSDT", T0, HOUR_END, source="live", statuses={})


# ------------------------------------------------------------------ status de arquivo (RF-176)


def test_archive_statuses() -> None:
    minutes = aggregate(
        [
            trade(1, T0 + 10 * SECOND_US),
            trade(2, T0 + 20 * SECOND_US),
            # minuto 1 sem negócios e sem salto: ok
            trade(3, T0 + 2 * MINUTE_US + 1),
            # salto 3 → 7: minutos 2 a 4 sem garantia
            trade(7, T0 + 4 * MINUTE_US + 1),
            trade(8, T0 + 5 * MINUTE_US + 1),
            # id ausente dentro do minuto 6
            trade(9, T0 + 6 * MINUTE_US + 1),
            trade(11, T0 + 6 * MINUTE_US + 2),
            trade(12, T0 + 7 * MINUTE_US + 1),
        ]
    )
    status = minutes.archive_statuses(T0, HOUR_END)
    assert [status[T0 + i * MINUTE_US] for i in range(9)] == [
        OK,
        OK,
        MISSING,
        MISSING,
        MISSING,
        OK,
        MISSING,
        OK,
        OK,
    ]
    assert minutes.first_edge() == TradeEdge(1, T0)
    assert minutes.last_edge() == TradeEdge(12, T0 + 7 * MINUTE_US)


def test_boundary_between_days() -> None:
    assert boundary_span(TradeEdge(10, T0), TradeEdge(11, T0 + MINUTE_US)) is None
    span = boundary_span(TradeEdge(10, T0 + 58 * MINUTE_US), TradeEdge(15, HOUR_END))
    assert span == (T0 + 58 * MINUTE_US, HOUR_END)
    minutes = aggregate([trade(10, T0 + 58 * MINUTE_US)])
    marked = mark_missing(
        minutes.table(
            "BTCUSDT",
            T0,
            HOUR_END,
            source="archive",
            statuses=dict.fromkeys(range(T0, HOUR_END, MINUTE_US), OK),
        ),
        *span,
    )
    statuses = marked["status"].to_pylist()
    assert statuses[:58] == [OK] * 58
    assert statuses[58:] == [MISSING, MISSING]
    assert marked.schema == MINUTE_TRADES
