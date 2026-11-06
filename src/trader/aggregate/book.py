"""Agregados por minuto do livro top 20: `minute_book` (doc 06 §5.6; RF-175).

Cada snapshot é atribuído ao minuto do seu `recv_ts_us`. As medidas são razões em FLOAT64
(a única exceção à regra de decimais até as features, doc 06 §5.6), calculadas sempre na mesma
ordem: níveis do melhor para o pior e snapshots em ordem de recebimento. Assim, as médias não
dependem da ordem do arquivo de entrada (RF-173).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from trader.aggregate.schema import MEASURES, MINUTE_BOOK, SCHEMA_VERSION
from trader.core.timeunits import MINUTE_US

COLUMNS = ("recv_ts_us", "last_update_id", "bid_px", "bid_qty", "ask_px", "ask_qty")
"""Colunas da camada normalizada (doc 06 §5.4) que a agregação lê."""

_DEPTHS = (1, 5, 10, 20)
_CHUNK_ROWS = 4_096
_FLOATS = pa.list_(pa.float64())


def snapshot_measures(
    bid_px: Sequence[float],
    bid_qty: Sequence[float],
    ask_px: Sequence[float],
    ask_qty: Sequence[float],
) -> tuple[float, ...] | None:
    """Medidas de um snapshot na ordem de `MEASURES`, ou `None` se um lado estiver vazio ou o
    livro estiver cruzado (snapshot `invalid`, doc 06 §8.1)."""
    if not bid_px or not ask_px:
        return None
    best_bid, best_ask = bid_px[0], ask_px[0]
    if best_bid >= best_ask:
        return None
    mid = (best_ask + best_bid) / 2
    imbalances = []
    bids = asks = 0.0
    level = 0
    for depth in _DEPTHS:
        while level < depth:
            if level < len(bid_qty):
                bids += bid_qty[level]
            if level < len(ask_qty):
                asks += ask_qty[level]
            level += 1
        imbalances.append(bids / (bids + asks))
    top_bid, top_ask = bid_qty[0], ask_qty[0]
    microprice = (best_ask * top_bid + best_bid * top_ask) / (top_bid + top_ask)
    return ((best_ask - best_bid) / mid, *imbalances, (microprice - mid) / mid)


@dataclass(slots=True)
class _Minute:
    count: int = 0
    last_recv: int | None = None
    valid: int = 0
    sums: list[float] = field(default_factory=lambda: [0.0] * len(MEASURES))
    mins: list[float] = field(default_factory=lambda: [0.0] * len(MEASURES))
    lasts: list[float] = field(default_factory=lambda: [0.0] * len(MEASURES))

    def add(self, recv_ts_us: int, values: tuple[float, ...] | None) -> None:
        self.count += 1
        self.last_recv = recv_ts_us
        if values is None:
            return
        first = self.valid == 0
        self.valid += 1
        for i, value in enumerate(values):
            self.sums[i] += value
            if first or value < self.mins[i]:
                self.mins[i] = value
            self.lasts[i] = value


def minute_book(
    snapshots: pa.Table,
    symbol: str,
    start_us: int,
    end_us: int,
    *,
    statuses: Mapping[int, str],
) -> pa.Table:
    """Uma linha por minuto de `[start_us, end_us)`. `snapshots` tem as colunas de `COLUMNS`;
    minuto sem status informado fica `missing`."""
    ordered = snapshots.select(list(COLUMNS)).sort_by(
        [("recv_ts_us", "ascending"), ("last_update_id", "ascending")]
    )
    minutes: dict[int, _Minute] = {}
    for chunk in ordered.to_batches(max_chunksize=_CHUNK_ROWS):
        recv = chunk.column("recv_ts_us").to_pylist()
        sides = [
            pc.cast(chunk.column(name), _FLOATS).to_pylist()
            for name in ("bid_px", "bid_qty", "ask_px", "ask_qty")
        ]
        for i, ts in enumerate(recv):
            minute_ts = ts - ts % MINUTE_US
            if not start_us <= minute_ts < end_us:
                raise ValueError(f"snapshot fora do período agregado (recv {ts})")
            minute = minutes.get(minute_ts)
            if minute is None:
                minute = minutes[minute_ts] = _Minute()
            bid_px, bid_qty, ask_px, ask_qty = (side[i] or [] for side in sides)
            minute.add(ts, snapshot_measures(bid_px, bid_qty, ask_px, ask_qty))
    columns: dict[str, list[Any]] = {name: [] for name in MINUTE_BOOK.names}
    empty = _Minute()
    for minute_ts in range(start_us, end_us, MINUTE_US):
        minute = minutes.get(minute_ts, empty)
        values: dict[str, Any] = {
            "symbol": symbol,
            "minute_ts_us": minute_ts,
            "snapshot_count": minute.count,
            "last_recv_ts_us": minute.last_recv,
            "status": statuses.get(minute_ts, "missing"),
            "schema_version": SCHEMA_VERSION,
        }
        for i, measure in enumerate(MEASURES):
            valid = minute.valid > 0
            values[f"{measure}_mean"] = minute.sums[i] / minute.valid if valid else None
            values[f"{measure}_min"] = minute.mins[i] if valid else None
            values[f"{measure}_last"] = minute.lasts[i] if valid else None
        for name, value in values.items():
            columns[name].append(value)
    return pa.Table.from_pydict(columns, schema=MINUTE_BOOK)
