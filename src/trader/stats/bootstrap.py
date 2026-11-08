"""Bootstrap em blocos de um dia UTC (doc 04 §10.1; RF-277, RF-279). Sem E/S.

Cada amostra sorteia, com reposição, tantos dias quantos a janela tem. A expectativa da amostra é a
média dos retornos das operações cuja saída cai nos dias sorteados, repetidas tantas vezes quantas
o dia saiu; o Sharpe, o dos retornos diários sorteados. O pareado usa os mesmos dias para as duas
estratégias.
"""

from dataclasses import dataclass

import numpy as np

from trader.backtest.metrics import ANNUALIZATION

RESAMPLES = 10_000
LOW, HIGH = 2.5, 97.5


@dataclass(frozen=True)
class Days:
    """Uma estratégia numa janela de D dias: retornos diários e operações pelo dia da saída."""

    daily: np.ndarray
    trade_returns: np.ndarray
    trade_days: np.ndarray
    """Posição, de 0 a D - 1, do dia da saída de cada operação."""

    def __post_init__(self) -> None:
        if len(self.trade_returns) != len(self.trade_days):
            raise ValueError("cada operação precisa do dia da saída")
        if len(self.trade_days) and (
            self.trade_days.min() < 0 or self.trade_days.max() >= len(self.daily)
        ):
            raise ValueError("operação fora dos dias da janela")

    def per_day(self) -> tuple[np.ndarray, np.ndarray]:
        """Soma dos retornos e número de operações em cada dia."""
        d = len(self.daily)
        sums = np.bincount(self.trade_days, weights=self.trade_returns, minlength=d)
        counts = np.bincount(self.trade_days, minlength=d)
        return sums, counts


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float | None
    high: float | None
    resamples: int
    empty: int
    """Amostras sem resultado (sem operação, ou Sharpe sem variação), que ficam fora."""

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "estimate": self.estimate,
            "low": self.low,
            "high": self.high,
            "resamples": self.resamples,
            "empty": self.empty,
        }


def draws(days: int, seed: int, resamples: int = RESAMPLES) -> np.ndarray:
    """Índices dos dias sorteados: uma linha por amostra."""
    if days == 0:
        raise ValueError("janela sem dias")
    rng = np.random.Generator(np.random.PCG64(seed))
    return rng.integers(0, days, size=(resamples, days))


def expectancy_samples(days: Days, idx: np.ndarray) -> np.ndarray:
    """Expectativa de cada amostra; NaN se ela não tiver operação."""
    sums, counts = days.per_day()
    total = sums[idx].sum(axis=1)
    n = counts[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, total / np.maximum(n, 1), np.nan)


def sharpe_samples(days: Days, idx: np.ndarray) -> np.ndarray:
    """Sharpe anualizado de cada amostra; NaN se os retornos sorteados não variarem."""
    sample = days.daily[idx]
    std = sample.std(axis=1, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(
            std > 0, sample.mean(axis=1) / np.where(std > 0, std, 1) * ANNUALIZATION, np.nan
        )


def interval(estimate: float, samples: np.ndarray) -> Interval:
    ok = samples[np.isfinite(samples)]
    if len(ok) == 0:
        return Interval(estimate, None, None, len(samples), len(samples))
    low, high = np.percentile(ok, [LOW, HIGH])
    return Interval(estimate, float(low), float(high), len(samples), len(samples) - len(ok))


def expectancy_interval(days: Days, seed: int, resamples: int = RESAMPLES) -> Interval:
    """Intervalo de 95% da expectativa por operação (IS-14, RF-277)."""
    idx = draws(len(days.daily), seed, resamples)
    estimate = float(days.trade_returns.mean()) if len(days.trade_returns) else float("nan")
    return interval(estimate, expectancy_samples(days, idx))


def paired(a: Days, b: Days, seed: int, resamples: int = RESAMPLES) -> tuple[Interval, Interval]:
    """Diferença a - b da expectativa e do Sharpe, com os mesmos dias sorteados (RF-279)."""
    if len(a.daily) != len(b.daily):
        raise ValueError("as duas estratégias precisam dos mesmos dias")
    idx = draws(len(a.daily), seed, resamples)
    expectancy = expectancy_samples(a, idx) - expectancy_samples(b, idx)
    sharpe = sharpe_samples(a, idx) - sharpe_samples(b, idx)
    point_e = _mean(a.trade_returns) - _mean(b.trade_returns)
    point_s = _sharpe(a.daily) - _sharpe(b.daily)
    return interval(point_e, expectancy), interval(point_s, sharpe)


def _mean(values: np.ndarray) -> float:
    return float(values.mean()) if len(values) else float("nan")


def _sharpe(daily: np.ndarray) -> float:
    std = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    return float(daily.mean()) / std * ANNUALIZATION if std > 0 else float("nan")
