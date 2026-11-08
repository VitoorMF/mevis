"""Métricas de todo experimento (doc 04 §10.1; RF-255). Sem E/S."""

import math
from dataclasses import asdict, dataclass

import numpy as np

from trader.backtest.simulator import Path

ANNUALIZATION = math.sqrt(365)
"""Sharpe anualizado a partir de retornos diários (doc 04 §10.1)."""


@dataclass(frozen=True)
class Metrics:
    trades: int
    expectancy_bps: float
    """Média dos retornos líquidos das operações, em pontos-base; zero sem operações."""
    total_return: float
    sharpe: float | None
    """`None` quando os retornos diários não variam (B0) ou há menos de dois dias."""
    max_drawdown: float
    hit_rate: float | None
    """Fração das operações com retorno líquido > 0; `None` sem operações."""
    exposure: float
    """Fração dos minutos da janela com posição aberta."""
    days: int
    stress_fallbacks: int

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def daily_returns(path: Path) -> np.ndarray:
    """Variação do capital marcado entre os fins de dias UTC consecutivos (doc 04 §10.1)."""
    ends = path.equity[path.day_last]
    return np.asarray(ends / np.concatenate([[1.0], ends[:-1]]) - 1)


def metrics(path: Path) -> Metrics:
    returns, equity = path.returns, path.equity
    trades = len(returns)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    daily = daily_returns(path)
    sharpe = None
    if len(daily) >= 2:
        deviation = float(np.std(daily, ddof=1))
        if deviation > 0:
            sharpe = float(np.mean(daily)) / deviation * ANNUALIZATION
    return Metrics(
        trades=trades,
        expectancy_bps=float(np.mean(returns)) * 1e4 if trades else 0.0,
        total_return=float(equity[-1]) - 1,
        sharpe=sharpe,
        max_drawdown=float(np.max(1 - equity / peak)),
        hit_rate=float(np.mean(returns > 0)) if trades else None,
        exposure=path.held_minutes / len(equity),
        days=len(daily),
        stress_fallbacks=path.fallbacks,
    )
