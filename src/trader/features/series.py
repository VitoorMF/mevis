"""Série de minutos que o cálculo das features recebe (RF-202; doc 04 §5.3). Sem E/S.

Por minuto e por canal: os valores, se o status é válido e quando o minuto fica disponível. A
montagem (`trader.pipeline.minutes`) grava a série em Arrow com os decimais da camada normalizada;
`series_from_table` é o único ponto em que eles viram FLOAT64 (RF-212; doc 06 §5.3).
"""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from trader.aggregate.schema import AMOUNT, BINS, MEASURES, PRICE, STATS
from trader.core.timeunits import MINUTE_US, SECOND_US

ARCHIVE_LATENCY_US = 1 * SECOND_US
"""L: dado sem horário de recebimento fica disponível no fim do minuto + L (doc 04 §3.5, PD-19)."""
VALID_STATUS = frozenset({"ok", "recovered"})
"""Status de minuto válido (doc 04 §3.4); os dias de arquivo só têm `ok` e `missing`."""

CANDLE_FIELDS = ("high", "low", "close", "volume", "trade_count")
TRADE_FIELDS = ("volume", "buy_volume", "quote_volume", "close")
TRADE_BINS = ("size_bins_count", "size_bins_volume")
BOOK_FIELDS = tuple(f"{m}_{s}" for m in MEASURES for s in STATS)

_NEVER = np.iinfo(np.int64).max
"""Disponibilidade de um minuto sem dado: nunca."""


def _channel_fields(prefix: str) -> list[pa.Field]:
    return [pa.field(f"{prefix}_valid", pa.bool_()), pa.field(f"{prefix}_available_us", pa.int64())]


MINUTES = pa.schema(
    [
        pa.field("minute_ts_us", pa.int64(), nullable=False),
        pa.field("candle_high", PRICE),
        pa.field("candle_low", PRICE),
        pa.field("candle_close", PRICE),
        pa.field("candle_volume", AMOUNT),
        pa.field("candle_trade_count", pa.int64()),
        *_channel_fields("candle"),
        pa.field("trades_volume", AMOUNT),
        pa.field("trades_buy_volume", AMOUNT),
        pa.field("trades_quote_volume", AMOUNT),
        pa.field("trades_close", PRICE),
        pa.field("trades_size_bins_count", pa.list_(pa.int32())),
        pa.field("trades_size_bins_volume", pa.list_(AMOUNT)),
        *_channel_fields("trades"),
        *[pa.field(f"book_{name}", pa.float64()) for name in BOOK_FIELDS],
        *_channel_fields("book"),
    ]
)
"""Uma linha por minuto UTC, sem lacunas; colunas nulas onde o canal não tem dado (RF-203)."""


@dataclass(frozen=True)
class Channel:
    values: Mapping[str, np.ndarray]
    """FLOAT64 com NaN onde não há dado; as faixas de tamanho são matrizes (minutos x faixas)."""
    valid: np.ndarray
    """Status válido no minuto (doc 04 §5.3), sem considerar a disponibilidade."""
    available_us: np.ndarray
    """Instante em que o minuto fica disponível para decisão (RF-205)."""

    def slice(self, start: int, stop: int) -> "Channel":
        return Channel(
            {k: v[start:stop] for k, v in self.values.items()},
            self.valid[start:stop],
            self.available_us[start:stop],
        )


@dataclass(frozen=True)
class MinuteSeries:
    minute_ts_us: np.ndarray
    candles: Channel
    trades: Channel
    book: Channel

    def __post_init__(self) -> None:
        ts = self.minute_ts_us
        if ts.ndim != 1 or (len(ts) > 1 and not np.all(np.diff(ts) == MINUTE_US)):
            raise ValueError("a série precisa ter um minuto por linha, sem lacunas nem repetições")
        if len(ts) and ts[0] % MINUTE_US:
            raise ValueError("os minutos começam no segundo zero")
        for channel in (self.candles, self.trades, self.book):
            arrays = [channel.valid, channel.available_us, *channel.values.values()]
            if any(a.shape[0] != len(ts) for a in arrays):
                raise ValueError("todas as colunas da série têm um valor por minuto")

    def __len__(self) -> int:
        return len(self.minute_ts_us)

    def slice(self, start: int, stop: int) -> "MinuteSeries":
        """Trecho `[start, stop)`, como o cálculo ao vivo da V0.3 verá (RF-206)."""
        return MinuteSeries(
            self.minute_ts_us[start:stop],
            self.candles.slice(start, stop),
            self.trades.slice(start, stop),
            self.book.slice(start, stop),
        )


def series_from_table(table: pa.Table) -> MinuteSeries:
    """Converte a série montada (esquema `MINUTES`) para arrays; os decimais viram FLOAT64."""
    table = table.select(MINUTES.names)
    rows = table.num_rows

    def floats(name: str) -> np.ndarray:
        column = table.column(name)
        return np.asarray(pc.cast(column, pa.float64()).to_numpy(zero_copy_only=False), np.float64)

    def channel(prefix: str, names: tuple[str, ...], extra: dict[str, np.ndarray]) -> Channel:
        values = {name: floats(f"{prefix}_{name}") for name in names} | extra
        valid = np.asarray(table.column(f"{prefix}_valid").fill_null(False), dtype=bool)
        available = np.asarray(table.column(f"{prefix}_available_us").fill_null(_NEVER), np.int64)
        return Channel(values, valid, available)

    dtypes = {"size_bins_count": np.int32, "size_bins_volume": np.float64}
    bins = {name: _bins(table.column(f"trades_{name}"), rows, dtypes[name]) for name in TRADE_BINS}
    candles = channel("candle", CANDLE_FIELDS, {})
    trades = channel("trades", TRADE_FIELDS, bins)
    book = channel("book", BOOK_FIELDS, {})
    minutes = np.asarray(table.column("minute_ts_us"), np.int64)
    return MinuteSeries(minutes, candles, trades, book)


def _bins(column: pa.ChunkedArray, rows: int, dtype: type) -> np.ndarray:
    """Lista de faixas por minuto → matriz (minutos x faixas); minuto sem lista fica com zeros."""
    present = np.asarray(column.is_valid(), dtype=bool)
    flat = pc.list_flatten(column)
    if pa.types.is_decimal(flat.type):
        flat = pc.cast(flat, pa.float64())
    values = np.asarray(flat.to_numpy(zero_copy_only=False), dtype=dtype).reshape(-1, BINS)
    if present.all():
        return values
    matrix: np.ndarray = np.zeros((rows, BINS), dtype=dtype)
    matrix[present] = values
    return matrix
