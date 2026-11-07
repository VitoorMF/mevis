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
    # empates de horário com ids fora de ordem, para o preço de execução (RF-221)
    rows += [
        trade(10_000 - i, T0 + (i // 3) * 900_000 + 2_600_000, f"{90 + i % 7}.5")
        for i in range(300)
    ]
    reference = aggregate(rows).table("BTCUSDT", T0, HOUR_END, source="live", statuses={})
    shuffled = rows[:]
    rng.shuffle(shuffled)
    batches = [shuffled[i : i + 137] for i in range(0, len(shuffled), 137)]
    other = aggregate(*batches).table("BTCUSDT", T0, HOUR_END, source="live", statuses={})
    assert other.equals(reference)
    assert reference.schema == MINUTE_TRADES


def test_execution_price_is_the_first_trade_from_each_offset() -> None:
    """RF-221: primeiro negócio com `trade_ts_us` >= início do minuto + 2,5 s e + 4 s."""
    minutes = aggregate(
        [
            trade(1, T0 + 2_499_999, "100"),
            trade(2, T0 + 2_500_000, "101"),
            trade(3, T0 + 3_999_999, "102"),
            trade(4, T0 + 4_000_000, "103"),
            trade(5, T0 + 50 * SECOND_US, "104"),
            # minuto 1: só antes de 2,5 s, então sem preço de execução
            trade(6, T0 + MINUTE_US + 2_499_999, "105"),
            # minuto 2: entre 2,5 s e 4 s, só o preço base
            trade(7, T0 + 2 * MINUTE_US + 3 * SECOND_US, "106"),
            # o negócio do minuto seguinte não conta para o minuto 2
            trade(8, T0 + 3 * MINUTE_US + 4 * SECOND_US, "107"),
        ]
    )
    rows = minutes.table("BTCUSDT", T0, HOUR_END, source="live", statuses={}).to_pylist()

    def execution(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["exec_price_2500ms"],
            row["exec_ts_2500ms_us"],
            row["exec_price_4000ms"],
            row["exec_ts_4000ms_us"],
        )

    assert execution(rows[0]) == (Decimal(101), T0 + 2_500_000, Decimal(103), T0 + 4_000_000)
    assert execution(rows[1]) == (None, None, None, None)
    second = T0 + 2 * MINUTE_US + 3 * SECOND_US
    assert execution(rows[2]) == (Decimal(106), second, None, None)
    third = T0 + 3 * MINUTE_US + 4 * SECOND_US
    assert execution(rows[3]) == (Decimal(107), third, Decimal(107), third)
    assert execution(rows[4]) == (None, None, None, None)


def test_execution_price_ties_and_batches() -> None:
    """No mesmo `trade_ts_us`, vale o menor `trade_id`, em qualquer ordem e divisão em lotes."""
    ts = T0 + 3 * SECOND_US
    rows = [
        trade(30, ts + 1, "99"),
        trade(21, ts, "102"),
        trade(20, ts, "101"),
        trade(19, ts + 5, "98"),
    ]
    for batches in ([rows], [[row] for row in rows], [rows[::-1]], [rows[2:], rows[:2]]):
        first = aggregate(*batches).table("BTCUSDT", T0, HOUR_END, source="live", statuses={})
        assert first.slice(0, 1).to_pylist()[0]["exec_price_2500ms"] == Decimal(101)
        assert first.slice(0, 1).to_pylist()[0]["exec_ts_2500ms_us"] == ts


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
