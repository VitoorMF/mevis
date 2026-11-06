"""Agregados por minuto dos negócios: `minute_trades` (doc 06 §5.6; RF-171 a RF-178).

Recebe lotes do Arrow com as colunas da camada normalizada e acumula, por minuto do
`trade_ts_us`, contagens, somas exatas em DECIMAL, o candle reconstruído (RF-171) e as faixas de
tamanho (DEC-31). Cada lote é resumido e descartado, então a memória depende do número de
minutos, não do de negócios (RNF-027). As somas são exatas e o candle usa os ids (abertura no
menor, fechamento no maior), então o resultado não depende da ordem dos lotes nem das linhas
(RF-173).
"""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, localcontext
from fractions import Fraction
from math import ceil, isqrt
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from trader.aggregate.schema import BIN_MIN, BINS, MINUTE_TRADES, SCHEMA_VERSION
from trader.core.timeunits import MINUTE_US

COLUMNS = ("trade_id", "price", "qty", "quote_qty", "trade_ts_us", "recv_ts_us", "aggressor")
"""Colunas da camada normalizada (doc 06 §5.4) que a agregação lê."""

OK = "ok"
MISSING = "missing"

_EXACT = Context(prec=38, traps=[Inexact, InvalidOperation, Overflow])
_ZERO = Decimal("0E-8")


def _fourth_root_ceil(value: int) -> int:
    root = isqrt(isqrt(value))
    while root**4 < value:
        root += 1
    return root


THRESHOLDS = tuple(
    _fourth_root_ceil(ceil(Fraction(10**32) * Fraction(2) ** (BIN_MIN + i))) for i in range(BINS)
)
"""Menor quantidade de cada faixa, em unidades de 1e-8 BTC: o menor `n` com `n**4 >= 2**b * 10**32`.

Faixa `b` é `2**(b/4) <= qty < 2**((b+1)/4)`, isto é, `b = floor(4 * log2(qty))`. Com inteiros, a
faixa não depende do arredondamento do logaritmo em cada máquina (RF-173)."""

_THRESHOLDS = pa.array(THRESHOLDS, pa.int64())
_UNITS = pa.scalar(Decimal(10**8), pa.decimal128(9, 0))


def size_bin(qty: Decimal) -> int:
    """Índice (0 a 100) da faixa de tamanho de uma quantidade positiva com até 8 casas."""
    units = int(qty.scaleb(8))
    return min(max(bisect_right(THRESHOLDS, units) - 1, 0), BINS - 1)


def size_bins(qty: pa.ChunkedArray | pa.Array) -> pa.Array:
    """`size_bin` vetorizado: estimativa pelo logaritmo e correção exata pelos limites."""
    units = pc.cast(pc.multiply(qty, _UNITS), pa.int64())
    approx = pc.cast(pc.floor(pc.multiply(pc.log2(pc.cast(qty, pa.float64())), 4.0)), pa.int64())
    index = pc.min_element_wise(pc.max_element_wise(pc.subtract(approx, BIN_MIN), 0), BINS - 1)
    down = pc.and_(pc.greater(index, 0), pc.less(units, pc.take(_THRESHOLDS, index)))
    index = pc.subtract(index, pc.cast(down, pa.int64()))
    above = pc.take(_THRESHOLDS, pc.min_element_wise(pc.add(index, 1), BINS - 1))
    up = pc.and_(pc.less(index, BINS - 1), pc.greater_equal(units, above))
    index = pc.add(index, pc.cast(up, pa.int64()))
    low_ok = pc.or_(pc.equal(index, 0), pc.greater_equal(units, pc.take(_THRESHOLDS, index)))
    above = pc.take(_THRESHOLDS, pc.min_element_wise(pc.add(index, 1), BINS - 1))
    high_ok = pc.or_(pc.equal(index, BINS - 1), pc.less(units, above))
    if not pc.all(pc.and_(low_ok, high_ok)).as_py():
        raise ValueError("faixa de tamanho fora dos limites exatos")
    return pc.cast(index, pa.int32())


@dataclass(frozen=True, slots=True)
class TradeEdge:
    """Primeiro ou último negócio de um período, para conferir a continuidade com o vizinho."""

    trade_id: int
    minute_ts_us: int


@dataclass(slots=True)
class _Minute:
    count: int = 0
    buy_count: int = 0
    volume: Decimal = _ZERO
    buy_volume: Decimal = _ZERO
    quote_volume: Decimal = _ZERO
    buy_quote_volume: Decimal = _ZERO
    first_id: int = 0
    open: Decimal = _ZERO
    last_id: int = 0
    close: Decimal = _ZERO
    high: Decimal = _ZERO
    low: Decimal = _ZERO
    last_recv: int | None = None
    bins_count: list[int] = field(default_factory=lambda: [0] * BINS)
    bins_volume: list[Decimal] = field(default_factory=lambda: [_ZERO] * BINS)


class TradeMinutes:
    """Acumula lotes de negócios normalizados por minuto do `trade_ts_us`."""

    def __init__(self) -> None:
        self._minutes: dict[int, _Minute] = {}
        self.rows = 0

    def add(self, batch: pa.RecordBatch | pa.Table) -> None:
        table = pa.Table.from_batches([batch]) if isinstance(batch, pa.RecordBatch) else batch
        if table.num_rows == 0:
            return
        self.rows += table.num_rows
        qty, quote = table["qty"], table["quote_qty"]
        buy = pc.equal(table["aggressor"], "B")
        work = pa.table(
            {
                "minute": pc.multiply(pc.divide(table["trade_ts_us"], MINUTE_US), MINUTE_US),
                "trade_id": table["trade_id"],
                "price": table["price"],
                "qty": qty,
                "quote_qty": quote,
                "buy": pc.cast(buy, pa.int64()),
                "buy_qty": pc.if_else(buy, qty, pa.scalar(_ZERO, qty.type)),
                "buy_quote": pc.if_else(buy, quote, pa.scalar(_ZERO, quote.type)),
                "recv": table["recv_ts_us"],
                "bin": size_bins(qty),
            }
        )
        grouped = work.group_by("minute", use_threads=False).aggregate(
            [
                ("trade_id", "count"),
                ("buy", "sum"),
                ("qty", "sum"),
                ("buy_qty", "sum"),
                ("quote_qty", "sum"),
                ("buy_quote", "sum"),
                ("trade_id", "min"),
                ("trade_id", "max"),
                ("price", "min"),
                ("price", "max"),
                ("recv", "max"),
            ]
        )
        ends = pa.concat_arrays(
            [grouped["trade_id_min"].combine_chunks(), grouped["trade_id_max"].combine_chunks()]
        )
        edge_rows = work.select(["trade_id", "price"]).filter(
            pc.is_in(work["trade_id"], value_set=ends)
        )
        prices = dict(
            zip(edge_rows["trade_id"].to_pylist(), edge_rows["price"].to_pylist(), strict=True)
        )
        bins = work.group_by(["minute", "bin"], use_threads=False).aggregate(
            [("qty", "sum"), ("qty", "count")]
        )
        with localcontext(_EXACT):
            for row in grouped.to_pylist():
                self._merge(row, prices)
            for row in bins.to_pylist():
                minute = self._minutes[row["minute"]]
                minute.bins_count[row["bin"]] += row["qty_count"]
                minute.bins_volume[row["bin"]] += row["qty_sum"]

    def _merge(self, row: Mapping[str, Any], prices: Mapping[int, Decimal]) -> None:
        minute = self._minutes.get(row["minute"])
        first_id, last_id = row["trade_id_min"], row["trade_id_max"]
        if minute is None:
            minute = _Minute(
                first_id=first_id,
                open=prices[first_id],
                last_id=last_id,
                close=prices[last_id],
                high=row["price_max"],
                low=row["price_min"],
            )
            self._minutes[row["minute"]] = minute
        else:
            if first_id < minute.first_id:
                minute.first_id, minute.open = first_id, prices[first_id]
            if last_id > minute.last_id:
                minute.last_id, minute.close = last_id, prices[last_id]
            minute.high = max(minute.high, row["price_max"])
            minute.low = min(minute.low, row["price_min"])
        minute.count += row["trade_id_count"]
        minute.buy_count += row["buy_sum"]
        minute.volume += row["qty_sum"]
        minute.buy_volume += row["buy_qty_sum"]
        minute.quote_volume += row["quote_qty_sum"]
        minute.buy_quote_volume += row["buy_quote_sum"]
        recv = row["recv_max"]
        if recv is not None and (minute.last_recv is None or recv > minute.last_recv):
            minute.last_recv = recv

    # ------------------------------------------------------------------ continuidade (arquivo)

    def first_edge(self) -> TradeEdge | None:
        if not self._minutes:
            return None
        minute = min(self._minutes)
        return TradeEdge(self._minutes[minute].first_id, minute)

    def last_edge(self) -> TradeEdge | None:
        if not self._minutes:
            return None
        minute = max(self._minutes)
        return TradeEdge(self._minutes[minute].last_id, minute)

    def archive_statuses(self, start_us: int, end_us: int) -> dict[int, str]:
        """Status de dias de arquivo, que não têm máscara (doc 06 §5.6; RF-176).

        `missing` em todos os minutos entre dois negócios de ids não consecutivos, incluindo os
        minutos desses dois negócios (os ids ausentes podem estar em qualquer um deles), e no
        minuto com id ausente entre os seus próprios negócios; `ok` nos demais, inclusive sem
        negócios. As fronteiras com os dias vizinhos são tratadas por `boundary_span`.
        """
        status = dict.fromkeys(range(start_us, end_us, MINUTE_US), OK)
        previous: tuple[int, _Minute] | None = None
        for minute_ts in sorted(self._minutes):
            minute = self._minutes[minute_ts]
            if minute.last_id - minute.first_id + 1 != minute.count:
                status[minute_ts] = MISSING
            if previous is not None and minute.first_id != previous[1].last_id + 1:
                for ts in range(previous[0], minute_ts + MINUTE_US, MINUTE_US):
                    status[ts] = MISSING
            previous = (minute_ts, minute)
        return status

    # ------------------------------------------------------------------ saída

    def table(
        self,
        symbol: str,
        start_us: int,
        end_us: int,
        *,
        source: str,
        statuses: Mapping[int, str],
    ) -> pa.Table:
        """Uma linha por minuto de `[start_us, end_us)`, inclusive sem negócios. Minuto sem status
        informado fica `missing`."""
        outside = sorted(m for m in self._minutes if not start_us <= m < end_us)
        if outside:
            raise ValueError(f"negócio fora do período agregado (minuto {outside[0]})")
        empty = _Minute()
        columns: dict[str, list[Any]] = {name: [] for name in MINUTE_TRADES.names}
        for minute_ts in range(start_us, end_us, MINUTE_US):
            minute = self._minutes.get(minute_ts, empty)
            traded = minute.count > 0
            values = {
                "symbol": symbol,
                "minute_ts_us": minute_ts,
                "trade_count": minute.count,
                "buy_count": minute.buy_count,
                "volume": minute.volume,
                "buy_volume": minute.buy_volume,
                "quote_volume": minute.quote_volume,
                "buy_quote_volume": minute.buy_quote_volume,
                "open": minute.open if traded else None,
                "high": minute.high if traded else None,
                "low": minute.low if traded else None,
                "close": minute.close if traded else None,
                "first_trade_id": minute.first_id if traded else None,
                "last_trade_id": minute.last_id if traded else None,
                "last_recv_ts_us": minute.last_recv,
                "size_bins_count": minute.bins_count,
                "size_bins_volume": minute.bins_volume,
                "status": statuses.get(minute_ts, MISSING),
                "source": source,
                "schema_version": SCHEMA_VERSION,
            }
            for name, value in values.items():
                columns[name].append(value)
        return pa.Table.from_pydict(columns, schema=MINUTE_TRADES)


def boundary_span(earlier: TradeEdge, later: TradeEdge) -> tuple[int, int] | None:
    """Minutos `[primeiro, último]` sem garantia de completude entre o último negócio de um dia e
    o primeiro do seguinte, ou `None` se os ids forem consecutivos (RF-176)."""
    if later.trade_id == earlier.trade_id + 1:
        return None
    return earlier.minute_ts_us, later.minute_ts_us


def mark_missing(table: pa.Table, first_us: int, last_us: int) -> pa.Table:
    """Marca `missing` os minutos de `[first_us, last_us]` de uma tabela `minute_trades`."""
    minutes = table["minute_ts_us"]
    inside = pc.and_(pc.greater_equal(minutes, first_us), pc.less_equal(minutes, last_us))
    status = pc.if_else(inside, MISSING, table["status"])
    index = table.schema.get_field_index("status")
    return table.set_column(index, table.schema.field("status"), status)
