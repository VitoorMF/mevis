"""Regressão logística e XGBoost da etapa 6 (doc 04 §7; RF-237, RF-244).

Os dois recebem e devolvem arrays `numpy`; o resto do código não conhece as bibliotecas. As mesmas
entradas dão as mesmas previsões: semente fixa e número fixo de threads.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

LOGISTIC = "logistic"
XGBOOST = "xgboost"
MODELS = (LOGISTIC, XGBOOST)
SEED = 0
THREADS = 4
TAUS = (0.50, 0.55, 0.60, 0.65, 0.70)
"""Grade de τ (doc 04 §7), mantida pelo DEC-41."""

Params = Mapping[str, float | int]

GRIDS: dict[str, tuple[dict[str, float | int], ...]] = {
    LOGISTIC: ({"C": 0.1}, {"C": 1.0}),
    XGBOOST: tuple(
        {"max_depth": depth, "n_estimators": trees, "learning_rate": 0.05, "subsample": 0.8}
        for depth in (2, 4)
        for trees in (200, 500)
    ),
}
"""Grades do doc 04 §7, na ordem que desempata a escolha (RF-239)."""


class Fitted(Protocol):
    def predict(self, x: np.ndarray) -> np.ndarray:
        """P(y = 1) em FLOAT64, uma por linha."""
        ...

    def importance(self) -> np.ndarray:
        """Uma por feature, na ordem das colunas (RF-244)."""
        ...


@dataclass(frozen=True)
class _Logistic:
    mean: np.ndarray
    scale: np.ndarray
    model: Any

    def predict(self, x: np.ndarray) -> np.ndarray:
        proba = self.model.predict_proba((x - self.mean) / self.scale)
        return np.asarray(proba[:, 1], dtype=np.float64)

    def importance(self) -> np.ndarray:
        return np.abs(np.asarray(self.model.coef_[0], dtype=np.float64))


@dataclass(frozen=True)
class _XGBoost:
    model: Any
    features: int

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(self.model.predict_proba(x)[:, 1], dtype=np.float64)

    def importance(self) -> np.ndarray:
        gain = self.model.get_booster().get_score(importance_type="gain")
        return np.array([float(gain.get(f"f{i}", 0.0)) for i in range(self.features)])


def fit(model: str, params: Params, x: np.ndarray, y: np.ndarray, seed: int = SEED) -> Fitted:
    """Ajusta um modelo da grade (RF-237)."""
    if len(np.unique(y)) < 2:
        raise ValueError("o trecho de ajuste precisa das duas classes do alvo")
    if model == LOGISTIC:
        from sklearn.linear_model import LogisticRegression

        mean = x.mean(axis=0)
        scale = x.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)
        logistic = LogisticRegression(C=float(params["C"]), solver="lbfgs", max_iter=1000)
        logistic.fit((x - mean) / scale, y)
        return _Logistic(mean, scale, logistic)
    if model == XGBOOST:
        from xgboost import XGBClassifier

        booster = XGBClassifier(
            objective="binary:logistic",
            tree_method="hist",
            max_depth=int(params["max_depth"]),
            n_estimators=int(params["n_estimators"]),
            learning_rate=float(params["learning_rate"]),
            subsample=float(params["subsample"]),
            random_state=seed,
            n_jobs=THREADS,
        )
        booster.fit(x, y)
        return _XGBoost(booster, x.shape[1])
    raise ValueError(f"modelo desconhecido: {model}")
