"""Dobras do walk-forward e purga (doc 04 §8.1; RF-236, RF-238). Sem E/S.

Os meses do desenvolvimento vão do dia 23 ao dia 22 do mês seguinte, em dias UTC inteiros. Cada
dobra tem um trecho de ajuste, um mês de validação, o treino final (ajuste e validação) e um mês de
teste; o treino cresce a cada dobra.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from trader.backtest.baselines import candidates
from trader.backtest.simulator import Market, Window

MONTH_START_DAY = 23
INITIAL_MONTHS = 3
"""Treino inicial da trilha A: 3 meses (doc 04 §8.1)."""

Period = tuple[date, date]


@dataclass(frozen=True)
class Fold:
    name: str
    fit: Window
    """Ajuste para a validação."""
    validation: Window
    train: Window
    """Treino final da dobra: ajuste e validação."""
    test: Window

    def labels(self) -> dict[str, str]:
        return {
            "fit": self.fit.label,
            "validation": self.validation.label,
            "train": self.train.label,
            "test": self.test.label,
        }


def months(first_day: date, last_day: date) -> list[Period]:
    """Meses do dia 23 ao dia 22: o período precisa começar num dia 23 e terminar num dia 22."""
    if first_day.day != MONTH_START_DAY or (last_day + timedelta(days=1)).day != MONTH_START_DAY:
        raise ValueError(f"o período precisa ir de um dia 23 a um dia 22: {first_day} a {last_day}")
    periods = []
    start = first_day
    while start <= last_day:
        year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        nxt = date(year, month, MONTH_START_DAY)
        periods.append((start, nxt - timedelta(days=1)))
        start = nxt
    if periods[-1][1] != last_day:
        raise ValueError(f"o período não termina no fim de um mês: {last_day}")
    return periods


def folds(market: Market, periods: Sequence[Period], initial: int = INITIAL_MONTHS) -> list[Fold]:
    """Uma dobra por período depois dos `initial` primeiros (RF-238)."""
    if len(periods) <= initial or initial < 2:
        raise ValueError(f"são precisos mais de {initial} períodos, com pelo menos 2 de treino")
    out = []
    for k in range(initial, len(periods)):
        out.append(
            Fold(
                name=f"D{k - initial + 1}",
                fit=market.window(periods[0][0], periods[k - 2][1]),
                validation=market.window(*periods[k - 1]),
                train=market.window(periods[0][0], periods[k - 1][1]),
                test=market.window(*periods[k]),
            )
        )
    return out


def points(market: Market, horizon: int, window: Window) -> np.ndarray:
    """Pontos elegíveis da janela cuja saída, no minuto t + 1 + H, cai dentro dela (RF-236).

    Num trecho de ajuste ou de treino, isso é a purga: saem os pontos cuja saída cai depois do
    início da janela seguinte. Numa validação ou num teste, são os pontos avaliados. É a mesma
    lista em que B2 sorteia."""
    return candidates(market, horizon, window)
