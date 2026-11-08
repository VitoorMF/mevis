"""Deflated Sharpe Ratio do IS-15 (doc 04 §10.1; DEC-41; RF-278). Sem E/S.

DSR = Φ((SR - SR0) · √(T - 1) / √(1 - A · SR + (K - 1) / 4 · SR²)), com
SR0 = √V · ((1 - E) · Φ⁻¹(1 - 1/N) + E · Φ⁻¹(1 - 1/(N · e))), em que E é a constante de
Euler-Mascheroni. SR, T, A e K vêm dos retornos diários da janela avaliada (Sharpe sem anualizar,
dias, assimetria e curtose); N é o número de tentativas da trilha e V a variância dos Sharpe
diários delas nos meses de validação.
"""

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import NormalDist

import numpy as np

EULER = 0.5772156649015329
_NORMAL = NormalDist()


@dataclass(frozen=True)
class Deflated:
    sharpe: float
    """Sharpe diário, sem anualizar."""
    days: int
    skewness: float
    kurtosis: float
    """Curtose comum (3 na normal), não o excesso."""
    trials: int
    variance: float
    expected_max: float
    """SR0: o maior Sharpe esperado entre N tentativas sem vantagem."""
    dsr: float | None
    """`None` se o denominador não for positivo ou houver menos de dois dias."""

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def expected_max_sharpe(variance: float, trials: int) -> float:
    """SR0; com uma tentativa só, não há seleção e SR₀ = 0."""
    if trials < 1 or variance < 0:
        raise ValueError("número de tentativas ou variância inválidos")
    if trials == 1:
        return 0.0
    inv = _NORMAL.inv_cdf
    return math.sqrt(variance) * (
        (1 - EULER) * inv(1 - 1 / trials) + EULER * inv(1 - 1 / (trials * math.e))
    )


def trial_variance(sharpes: Sequence[float | None]) -> float:
    """Variância, entre as tentativas, dos Sharpe diários; `None` (sem operação) vale zero."""
    values = np.array([0.0 if s is None else s for s in sharpes], dtype=np.float64)
    if len(values) < 2:
        return 0.0
    return float(values.var(ddof=1))


def deflated_sharpe(daily: np.ndarray, trials: int, variance: float) -> Deflated:
    t = len(daily)
    sr0 = expected_max_sharpe(variance, trials)
    std = float(daily.std(ddof=1)) if t > 1 else 0.0
    if std == 0:
        return Deflated(0.0, t, 0.0, 0.0, trials, variance, sr0, None)
    sr = float(daily.mean()) / std
    centered = daily - daily.mean()
    m2 = float(np.mean(centered**2))
    skew = float(np.mean(centered**3)) / m2**1.5
    kurt = float(np.mean(centered**4)) / m2**2
    denominator = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    dsr = None
    if denominator > 0:
        dsr = _NORMAL.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denominator))
    return Deflated(sr, t, skew, kurt, trials, variance, sr0, dsr)
