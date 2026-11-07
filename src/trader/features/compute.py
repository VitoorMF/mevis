"""Cálculo das 59 features e da validade por família (doc 04 §5.3; RF-201, RF-205 a RF-209).

Sem E/S. Notação do doc 04 §5.3: t é o último minuto fechado no instante da decisão
t_d = fim de t + 2 s, e J_w são os w minutos de t - w + 1 a t.

Disponibilidade (RF-205): o minuto t precisa estar disponível até 2 s depois do seu fim; um minuto
anterior, até 62 s depois do seu fim, que é o t_d do minuto seguinte. Um minuto mais atrasado que
isso fica fora de todos os pontos, o que é mais rígido que o necessário para os pontos mais
distantes, nunca menos; na prática não acontece, porque o atraso de negócios acima de 5 s já marca
o minuto como `late` na máscara (doc 06 §8.1).
"""

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from trader.aggregate.schema import BINS
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.features.series import ARCHIVE_LATENCY_US, BOOK_FIELDS, Channel, MinuteSeries
from trader.features.windows import (
    all_valid,
    enough_valid,
    lag,
    window_count,
    window_max,
    window_min,
    window_sum,
)

CATALOG_VERSION = 1
DECISION_DELAY_US = 2 * SECOND_US
LOOKBACK_MINUTES = 1500
"""A janela mais longa, a de `large_frac_60`: 60 + 1.440 minutos (doc 04 §5.1, regra 6)."""
LONG_PERCENT = 95
"""Mínimo de minutos válidos nas janelas de 240 e 1.440 minutos (doc 04 §3.4)."""
DAY_MINUTES = 1440
LARGE_PERCENTILE = 95

FAMILIES = ("candles", "volume", "flow", "book")


def _names() -> tuple[tuple[str, str], ...]:
    candles = [
        *(f"ret_{w}" for w in (1, 5, 15, 60, 240)),
        *(f"rvol_{w}" for w in (15, 60)),
        *(f"range_{w}" for w in (1, 15)),
        *(f"sma_dist_{w}" for w in (15, 60, 240)),
        "rsi_14",
        "range_pos_60",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
    ]
    volume = [
        *(f"vol_rel_{w}" for w in (1, 5, 15, 60)),
        *(f"count_rel_{w}" for w in (1, 15)),
        "vwret_15",
    ]
    flow = [
        *(f"buy_frac_{w}" for w in (1, 5, 15, 60)),
        *(f"signed_vol_{w}" for w in (1, 5, 15, 60)),
        *(f"large_frac_{w}" for w in (15, 60)),
        *(f"vwap_dist_{w}" for w in (15, 60)),
    ]
    book = [
        *(f"book_{name}" for name in BOOK_FIELDS),
        *(f"imb{k}_chg_{w}" for k in (1, 20) for w in (1, 5)),
    ]
    groups = zip(FAMILIES, (candles, volume, flow, book), strict=True)
    return tuple((name, family) for family, names in groups for name in names)


FEATURES = _names()
"""(nome, família), na ordem das colunas da saída (RF-209)."""


def catalog() -> dict[str, object]:
    """O que identifica o catálogo no manifesto dos datasets (RF-209; doc 04 §11)."""
    return {
        "version": CATALOG_VERSION,
        "features": [{"name": name, "family": family} for name, family in FEATURES],
        "decision_delay_s": DECISION_DELAY_US / SECOND_US,
        "archive_latency_s": ARCHIVE_LATENCY_US / SECOND_US,
        "lookback_minutes": LOOKBACK_MINUTES,
        "long_window_min_percent": LONG_PERCENT,
        "large_trade_percentile": LARGE_PERCENTILE,
    }


def catalog_id() -> str:
    encoded = json.dumps(catalog(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class FeatureFrame:
    minute_ts_us: np.ndarray
    decision_ts_us: np.ndarray
    values: dict[str, np.ndarray]
    """Uma coluna por feature, NaN onde o ponto não é válido na família (RF-208)."""
    valid: dict[str, np.ndarray]
    """Uma coluna por família (RF-207); os degraus são cumulativos (doc 04 §5.3)."""

    def to_table(self) -> pa.Table:
        columns: dict[str, pa.Array] = {
            "minute_ts_us": pa.array(self.minute_ts_us, pa.int64()),
            "decision_ts_us": pa.array(self.decision_ts_us, pa.int64()),
        }
        for name, _ in FEATURES:
            columns[name] = pa.array(self.values[name], pa.float64(), from_pandas=True)
        for family in FAMILIES:
            columns[f"valid_{family}"] = pa.array(self.valid[family], pa.bool_())
        return pa.table(columns)


@dataclass(frozen=True)
class _Ready:
    """Validade com disponibilidade: `now` para o minuto t, `prev` para os minutos anteriores."""

    prev: np.ndarray
    now: np.ndarray

    def masked(self, x: np.ndarray) -> np.ndarray:
        return np.where(self.prev, x, np.nan)

    def zeros(self, x: np.ndarray) -> np.ndarray:
        return np.where(self.prev, x, 0.0)


def _ready(minute_ts_us: np.ndarray, channel: Channel) -> _Ready:
    late = channel.available_us - (minute_ts_us + MINUTE_US)
    prev = channel.valid & (late <= MINUTE_US + DECISION_DELAY_US)
    return _Ready(prev=prev, now=prev & (late <= DECISION_DELAY_US))


def compute_features(series: MinuteSeries) -> FeatureFrame:
    """As features de cada minuto da série, no instante de decisão desse minuto (RF-201)."""
    ts = series.minute_ts_us
    with np.errstate(divide="ignore", invalid="ignore"):
        candle = _ready(ts, series.candles)
        trades = _ready(ts, series.trades)
        book = _ready(ts, series.book)
        values: dict[str, np.ndarray] = {}
        valid = {
            "candles": _candles(ts, series.candles, candle, values),
            "volume": _volume(series.candles, candle, values),
            "flow": _flow(series.trades, trades, values),
            "book": _book(series.book, book, values),
        }
        for name, family in FEATURES:
            values[name] = np.where(valid[family], values[name], np.nan)
    return FeatureFrame(ts, ts + MINUTE_US + DECISION_DELAY_US, values, valid)


def _defined(values: dict[str, np.ndarray], family: str) -> np.ndarray:
    """Regra 7 de doc 04 §5.1: feature indefinida torna o ponto inválido na família."""
    names = [name for name, fam in FEATURES if fam == family]
    ok: np.ndarray = np.logical_and.reduce([np.isfinite(values[name]) for name in names])
    return ok


def _long(ready: _Ready, w: int, end_lag: int = 0) -> np.ndarray:
    """≥ 95% de minutos válidos nos w minutos que terminam em t - `end_lag`."""
    ok: np.ndarray = enough_valid(window_count(ready.prev, w), w, LONG_PERCENT)
    return lag(ok, end_lag) if end_lag else ok


def _log_returns(c: np.ndarray) -> np.ndarray:
    r: np.ndarray = np.log(c / lag(c, 1))
    return r


def _candles(ts: np.ndarray, ch: Channel, ready: _Ready, out: dict[str, np.ndarray]) -> np.ndarray:
    c, h, low = (ready.masked(ch.values[k]) for k in ("close", "high", "low"))
    r = _log_returns(c)
    for w in (1, 5, 15, 60, 240):
        out[f"ret_{w}"] = np.log(c / lag(c, w))
    for w in (15, 60):
        out[f"rvol_{w}"] = np.sqrt(window_sum(r * r, w))
    for w in (1, 15):
        out[f"range_{w}"] = (window_max(h, w) - window_min(low, w)) / c
    closes = ready.zeros(ch.values["close"])
    for w in (15, 60, 240):
        mean = window_sum(closes, w) / window_count(ready.prev, w)
        out[f"sma_dist_{w}"] = c / mean - 1
    change = c - lag(c, 1)
    gains = window_sum(np.maximum(change, 0.0), 14)
    losses = window_sum(np.maximum(-change, 0.0), 14)
    total = gains + losses
    out["rsi_14"] = np.where(total > 0, 100 * gains / total, np.nan)
    lowest, highest = window_min(low, 60), window_max(h, 60)
    span = highest - lowest
    out["range_pos_60"] = np.where(span > 0, (c - lowest) / span, np.nan)
    end_s = (ts + MINUTE_US) // SECOND_US
    hour = (end_s % 86_400) / 3_600
    weekday = (end_s // 86_400 + 3) % 7  # 01/01/1970 foi quinta-feira; segunda = 0
    for name, angle in (("hour", 2 * np.pi * hour / 24), ("dow", 2 * np.pi * weekday / 7)):
        out[f"{name}_sin"], out[f"{name}_cos"] = np.sin(angle), np.cos(angle)
    ok: np.ndarray = (
        all_valid(ready.prev, ready.now, 60)
        & _long(ready, 240)
        & lag(ready.prev, 240)
        & _defined(out, "candles")
    )
    return ok


def _day_mean(x: np.ndarray, ready: _Ready) -> np.ndarray:
    """Média por minuto nos minutos válidos de J_1440 (v̄, n̄, V̄)."""
    total = window_sum(ready.zeros(x), DAY_MINUTES)
    mean: np.ndarray = total / window_count(ready.prev, DAY_MINUTES)
    return mean


def _volume(ch: Channel, ready: _Ready, out: dict[str, np.ndarray]) -> np.ndarray:
    v, n, c = (ready.masked(ch.values[k]) for k in ("volume", "trade_count", "close"))
    v_bar = _day_mean(ch.values["volume"], ready)
    n_bar = _day_mean(ch.values["trade_count"], ready)
    for w in (1, 5, 15, 60):
        out[f"vol_rel_{w}"] = np.where(v_bar > 0, window_sum(v, w) / (w * v_bar), np.nan)
    for w in (1, 15):
        out[f"count_rel_{w}"] = np.where(n_bar > 0, window_sum(n, w) / (w * n_bar), np.nan)
    weight = window_sum(v, 15)
    weighted = window_sum(v * _log_returns(c), 15)
    out["vwret_15"] = np.where(weight > 0, weighted / weight, np.nan)
    ok: np.ndarray = all_valid(ready.prev, ready.now, 59) & _long(ready, DAY_MINUTES)
    ok &= _defined(out, "volume")
    return ok


def _flow(ch: Channel, ready: _Ready, out: dict[str, np.ndarray]) -> np.ndarray:
    volume, buy, quote, close = (
        ready.masked(ch.values[k]) for k in ("volume", "buy_volume", "quote_volume", "close")
    )
    v_bar = _day_mean(ch.values["volume"], ready)
    signed = 2 * buy - volume  # B - S
    sums = {w: window_sum(volume, w) for w in (1, 5, 15, 60)}
    for w in (1, 5, 15, 60):
        out[f"buy_frac_{w}"] = np.where(sums[w] > 0, window_sum(buy, w) / sums[w], np.nan)
        out[f"signed_vol_{w}"] = np.where(v_bar > 0, window_sum(signed, w) / (w * v_bar), np.nan)
    above = _volume_above_bins(ch.values["size_bins_volume"])
    counts = np.where(ready.prev[:, None], ch.values["size_bins_count"], 0)
    cumulative = np.zeros((counts.shape[0] + 1, BINS), dtype=np.int64)
    np.cumsum(counts, axis=0, dtype=np.int64, out=cumulative[1:])
    del counts
    for w in (15, 60):
        threshold = _large_threshold(cumulative, w)
        large = _gather_window(above, threshold, w)
        out[f"large_frac_{w}"] = np.where(sums[w] > 0, large / sums[w], np.nan)
    for w in (15, 60):
        vwap = window_sum(quote, w) / sums[w]
        out[f"vwap_dist_{w}"] = np.where(sums[w] > 0, close / vwap - 1, np.nan)
    ok: np.ndarray = (
        all_valid(ready.prev, ready.now, 59)
        & _long(ready, DAY_MINUTES)
        & _long(ready, DAY_MINUTES, end_lag=15)
        & _long(ready, DAY_MINUTES, end_lag=60)
        & _defined(out, "flow")
    )
    return ok


def _volume_above_bins(volume: np.ndarray) -> np.ndarray:
    """[i, k] = volume do minuto i nas faixas acima de k (soma da faixa mais alta para baixo)."""
    above = np.zeros_like(volume)
    np.cumsum(volume[:, :0:-1], axis=1, out=above[:, -2::-1])  # da faixa mais alta até k + 1
    return above


def _large_threshold(cumulative: np.ndarray, w: int, chunk: int = 65_536) -> np.ndarray:
    """k*: faixa que contém o percentil 95 do tamanho, pela contagem nos 1.440 minutos anteriores
    a J_w (de t - w - 1.439 a t - w); -1 se não há minutos ou negócios suficientes."""
    n = cumulative.shape[0] - 1
    threshold = np.full(n, -1, dtype=np.int64)
    first = w + DAY_MINUTES - 1
    for start in range(first, n, chunk):
        t = np.arange(start, min(start + chunk, n))
        histogram = cumulative[t - w + 1] - cumulative[t - w - DAY_MINUTES + 1]
        total = histogram.sum(axis=1)
        reached = np.cumsum(histogram, axis=1) * 100 >= LARGE_PERCENTILE * total[:, None]
        threshold[t] = np.where(total > 0, np.argmax(reached, axis=1), -1)
    return threshold


def _gather_window(above: np.ndarray, threshold: np.ndarray, w: int) -> np.ndarray:
    """Σ above[i, k*(t)], i ∈ J_w, do minuto mais antigo ao mais novo (como `window_sum`)."""
    n = above.shape[0]
    total = np.full(n, np.nan)
    t = np.flatnonzero((threshold >= 0) & (np.arange(n) >= w - 1))
    acc = np.zeros(t.shape[0])
    for k in range(w):
        acc += above[t - w + 1 + k, threshold[t]]
    total[t] = acc
    return total


def _book(ch: Channel, ready: _Ready, out: dict[str, np.ndarray]) -> np.ndarray:
    for name in BOOK_FIELDS:
        out[f"book_{name}"] = ready.masked(ch.values[name])
    for k in (1, 20):
        last = ready.masked(ch.values[f"imbalance_{k}_last"])
        for w in (1, 5):
            out[f"imb{k}_chg_{w}"] = last - lag(last, w)
    ok: np.ndarray = ready.now & lag(ready.prev, 1) & lag(ready.prev, 5)
    ok &= _defined(out, "book")
    return ok
