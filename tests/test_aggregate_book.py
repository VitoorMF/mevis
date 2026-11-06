"""Agregados por minuto do livro top 20 (doc 06 §5.6; RF-175)."""

import random
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pytest

from trader.aggregate.book import minute_book, snapshot_measures
from trader.aggregate.schema import MEASURES, MINUTE_BOOK
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.storage import parquet

T0 = 1_792_764_000_000_000  # 2026-10-23 14:00 UTC
HOUR_END = T0 + 60 * MINUTE_US


def test_measures_of_a_snapshot() -> None:
    values = snapshot_measures([100.0, 99.0], [1.0, 3.0], [101.0, 102.0], [2.0, 1.0])
    assert values is not None
    measures = dict(zip(MEASURES, values, strict=True))
    mid = 100.5
    microprice = (101 * 1 + 100 * 2) / 3
    assert measures["spread_rel"] == pytest.approx(1 / mid)
    assert measures["imbalance_1"] == pytest.approx(1 / 3)
    assert measures["imbalance_5"] == pytest.approx(4 / 7)  # só os níveis que existem
    assert measures["imbalance_20"] == pytest.approx(4 / 7)
    assert measures["microprice_dist"] == pytest.approx((microprice - mid) / mid)


def test_crossed_or_one_sided_book_has_no_measures() -> None:
    assert snapshot_measures([101.0], [1.0], [101.0], [1.0]) is None
    assert snapshot_measures([], [], [101.0], [1.0]) is None


def snapshot(update_id: int, recv: int, bid: str = "100", ask: str = "101") -> dict[str, Any]:
    return {
        "last_update_id": update_id,
        "recv_ts_us": recv,
        "bid_px": [Decimal(bid), Decimal(bid) - 1],
        "bid_qty": [Decimal(update_id % 5 + 1), Decimal(2)],
        "ask_px": [Decimal(ask), Decimal(ask) + 1],
        "ask_qty": [Decimal(1), Decimal(update_id % 3 + 1)],
        "symbol": "BTCUSDT",
        "origin": "live",
        "provenance": "S1",
        "schema_version": 1,
    }


def test_minute_book() -> None:
    rows = [
        snapshot(1, T0 + 1 * SECOND_US),
        snapshot(2, T0 + 2 * SECOND_US, bid="102"),  # cruzado: conta, mas sem medidas
        snapshot(3, T0 + 3 * SECOND_US, bid="99"),
        snapshot(4, T0 + 2 * MINUTE_US + 5),
    ]
    snapshots = pa.Table.from_pylist(rows, schema=parquet.BOOK_TOP20)
    result = minute_book(snapshots, "BTCUSDT", T0, HOUR_END, statuses={T0: "ok"})
    assert result.schema == MINUTE_BOOK
    out = result.to_pylist()
    assert len(out) == 60
    first = out[0]
    assert (first["snapshot_count"], first["last_recv_ts_us"]) == (3, T0 + 3 * SECOND_US)
    spreads = [1 / 100.5, 2 / 100.0]
    assert first["spread_rel_mean"] == pytest.approx(sum(spreads) / 2)
    assert first["spread_rel_min"] == pytest.approx(min(spreads))
    assert first["spread_rel_last"] == pytest.approx(spreads[1])
    assert first["status"] == "ok"
    assert (out[1]["snapshot_count"], out[1]["imbalance_1_mean"], out[1]["status"]) == (
        0,
        None,
        "missing",
    )
    assert out[2]["snapshot_count"] == 1


def test_minute_book_is_deterministic_in_any_input_order() -> None:
    """RF-173: as médias somam os snapshots em ordem de recebimento, seja qual for a do arquivo."""
    rng = random.Random(5)
    rows = [
        snapshot(i, T0 + i * 97_000, bid=f"{rng.uniform(99, 100):.2f}", ask="100.5")
        for i in range(1, 3_000)
    ]
    ordered = pa.Table.from_pylist(rows, schema=parquet.BOOK_TOP20)
    rng.shuffle(rows)
    shuffled = pa.Table.from_pylist(rows, schema=parquet.BOOK_TOP20)
    expected = minute_book(ordered, "BTCUSDT", T0, HOUR_END, statuses={})
    assert minute_book(shuffled, "BTCUSDT", T0, HOUR_END, statuses={}).equals(expected)


def test_snapshot_outside_the_period_is_refused() -> None:
    snapshots = pa.Table.from_pylist([snapshot(1, HOUR_END)], schema=parquet.BOOK_TOP20)
    with pytest.raises(ValueError, match="fora do período"):
        minute_book(snapshots, "BTCUSDT", T0, HOUR_END, statuses={})
