"""Walk-forward de um degrau, um horizonte e um modelo (doc 04 §7 e §8; RF-235 a RF-244). Sem E/S.

Em cada dobra: ajusta cada combinação da grade no trecho de ajuste e prevê a validação; escolhe os
hiperparâmetros pelo log loss e o τ pelo Sharpe no cenário base; ajusta de novo no treino final e
prevê o teste. Sem τ escolhido, a estratégia não opera no teste (abstenção), mas o teste ainda é
previsto para os diagnósticos. As escolhas nunca leem o teste (RF-242).
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from trader.backtest.metrics import Metrics, metrics
from trader.backtest.simulator import BASE, SCENARIOS, Market, Path, Run, Window, chain, simulate
from trader.features.compute import FEATURES
from trader.models.estimators import GRIDS, SEED, TAUS, fit
from trader.models.folds import Fold, points
from trader.models.selection import (
    auc,
    choose_hyperparameters,
    choose_tau,
    log_loss,
)

RUNGS = {
    "B4": ("candles",),
    "B5": ("candles", "volume"),
    "B6": ("candles", "volume", "flow"),
}
"""Famílias de cada degrau da trilha A (doc 04 §2 e §7; RF-235)."""
VALIDATION = 0
TEST = 1
"""Trechos das previsões guardadas."""


def rung_features(rung: str) -> list[str]:
    """Features do catálogo nas famílias do degrau, na ordem do catálogo (RF-235)."""
    if rung not in RUNGS:
        raise ValueError(f"degrau desconhecido: {rung}")
    return [name for name, family in FEATURES if family in RUNGS[rung]]


@dataclass(frozen=True)
class Task:
    rung: str
    horizon: int
    model: str
    seed: int = SEED


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    losses: list[float]
    """Log loss de cada combinação da grade na validação."""
    aucs: list[float | None]
    chosen: int
    taus: list[tuple[float, Metrics]]
    """Cada τ na validação, com a combinação escolhida, no cenário base."""
    tau: float | None
    """`None` é a abstenção (RF-241)."""
    test: Run
    test_log_loss: float
    test_auc: float | None
    importance: np.ndarray
    seconds: float

    @property
    def abstained(self) -> bool:
        return self.tau is None


@dataclass(frozen=True)
class Trial:
    hyperparameters: Mapping[str, float | int]
    threshold: float
    metrics: dict[str, dict[str, dict[str, Any]]]
    """Por janela de validação e cenário; `validation` junta os meses (DSR, RF-278)."""


@dataclass(frozen=True)
class Predictions:
    minute: np.ndarray
    """Índice do minuto no dataset."""
    fold: np.ndarray
    segment: np.ndarray
    combo: np.ndarray
    p: np.ndarray


@dataclass
class WalkForward:
    task: Task
    features: list[str]
    grid: tuple[Mapping[str, float | int], ...]
    taus: tuple[float, ...]
    folds: list[FoldResult]
    trials: list[Trial]
    predictions: Predictions
    seconds: dict[str, float] = field(default_factory=dict)

    def combined(self) -> dict[str, Metrics]:
        """Métricas das dobras de teste juntas, por cenário (RF-243)."""
        names = self.folds[0].test.paths
        return {name: metrics(chain([f.test.paths[name] for f in self.folds])) for name in names}

    def outcome(self) -> dict[str, Any]:
        """Resultado do procedimento, em JSON, para o registro (RF-245)."""
        return {
            "rung": self.task.rung,
            "horizon": self.task.horizon,
            "model": self.task.model,
            "seed": self.task.seed,
            "features": self.features,
            "grid": [dict(g) for g in self.grid],
            "thresholds": list(self.taus),
            "folds": [
                {
                    "fold": f.fold.name,
                    "windows": f.fold.labels(),
                    "validation_log_loss": f.losses,
                    "validation_auc": f.aucs,
                    "chosen": f.chosen,
                    "hyperparameters": dict(self.grid[f.chosen]),
                    "validation_thresholds": [
                        {"threshold": tau, "base": m.to_dict()} for tau, m in f.taus
                    ],
                    "threshold": f.tau,
                    "abstained": f.abstained,
                    "test": {name: metrics(path).to_dict() for name, path in f.test.paths.items()},
                    "test_log_loss": f.test_log_loss,
                    "test_auc": f.test_auc,
                    "importance": dict(zip(self.features, map(float, f.importance), strict=True)),
                    "seconds": f.seconds,
                }
                for f in self.folds
            ],
            "combined": {name: m.to_dict() for name, m in self.combined().items()},
        }


def labels_at(target: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Alvo nos pontos; um ponto elegível sempre tem alvo 0 ou 1."""
    y = np.asarray(target[idx], dtype=np.float64)
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("ponto elegível sem alvo válido")
    return y


def signal_at(n: int, idx: np.ndarray, p: np.ndarray, tau: float | None) -> np.ndarray:
    """Sinal por minuto: p > τ nos pontos avaliados; nenhum sinal na abstenção."""
    signal = np.zeros(n, dtype=bool)
    if tau is not None:
        signal[idx] = p > tau
    return signal


def walk_forward(
    market: Market,
    x: np.ndarray,
    target: np.ndarray,
    task: Task,
    folds: Sequence[Fold],
    features: Sequence[str],
    grid: Sequence[Mapping[str, float | int]] | None = None,
    taus: Sequence[float] = TAUS,
) -> WalkForward:
    """Roda as dobras de um degrau, um horizonte e um modelo.

    `x` tem uma linha por minuto do dataset e as colunas `features`; `target` é o alvo do
    horizonte (NaN onde não é válido)."""
    if x.shape != (len(market.minute_ts_us), len(features)):
        raise ValueError("a matriz de features não bate com o dataset e a lista de features")
    combos = tuple(grid if grid is not None else GRIDS[task.model])
    h, n = task.horizon, len(market.minute_ts_us)
    results: list[FoldResult] = []
    validation_paths: dict[tuple[int, float], list[Path]] = {}
    trial_windows: dict[tuple[int, float], dict[str, dict[str, dict[str, Any]]]] = {}
    pred: dict[str, list[np.ndarray]] = {k: [] for k in ("minute", "fold", "segment", "combo", "p")}

    def keep(idx: np.ndarray, fold: int, segment: int, combo: int, p: np.ndarray) -> None:
        pred["minute"].append(idx)
        pred["fold"].append(np.full(len(idx), fold, dtype=np.int8))
        pred["segment"].append(np.full(len(idx), segment, dtype=np.int8))
        pred["combo"].append(np.full(len(idx), combo, dtype=np.int8))
        pred["p"].append(p)

    for k, fold in enumerate(folds):
        began = time.monotonic()
        fit_idx = points(market, h, fold.fit)
        val_idx = points(market, h, fold.validation)
        x_fit, y_fit = _rows(x, fit_idx), labels_at(target, fit_idx)
        y_val = labels_at(target, val_idx)
        losses: list[float] = []
        aucs: list[float | None] = []
        by_combo: list[list[tuple[float, Metrics]]] = []
        for i, params in enumerate(combos):
            model = fit(task.model, params, x_fit, y_fit, task.seed)
            p_val = model.predict(_rows(x, val_idx))
            keep(val_idx, k, VALIDATION, i, p_val)
            losses.append(log_loss(y_val, p_val))
            aucs.append(auc(y_val, p_val))
            rows = []
            for tau in taus:
                run = simulate(
                    market, signal_at(n, val_idx, p_val, tau), h, fold.validation, (BASE,)
                )
                path = run.paths[BASE.name]
                m = metrics(path)
                rows.append((tau, m))
                validation_paths.setdefault((i, tau), []).append(path)
                extra = {"log_loss": losses[-1], "auc": aucs[-1]}
                trial_windows.setdefault((i, tau), {})[fold.validation.label] = {
                    BASE.name: m.to_dict() | extra
                }
            by_combo.append(rows)
        chosen = choose_hyperparameters(losses)
        chosen_tau = choose_tau(by_combo[chosen])

        train_idx = points(market, h, fold.train)
        test_idx = points(market, h, fold.test)
        model = fit(
            task.model, combos[chosen], _rows(x, train_idx), labels_at(target, train_idx), task.seed
        )
        p_test = model.predict(_rows(x, test_idx))
        keep(test_idx, k, TEST, chosen, p_test)
        y_test = labels_at(target, test_idx)
        test = simulate(market, signal_at(n, test_idx, p_test, chosen_tau), h, fold.test, SCENARIOS)
        results.append(
            FoldResult(
                fold=fold,
                losses=losses,
                aucs=aucs,
                chosen=chosen,
                taus=by_combo[chosen],
                tau=chosen_tau,
                test=test,
                test_log_loss=log_loss(y_test, p_test),
                test_auc=auc(y_test, p_test),
                importance=model.importance(),
                seconds=time.monotonic() - began,
            )
        )

    trials = []
    for (i, tau), windows in trial_windows.items():
        joined = metrics(chain(validation_paths[(i, tau)]))
        windows["validation"] = {BASE.name: joined.to_dict()}
        trials.append(Trial(combos[i], tau, windows))
    return WalkForward(
        task=task,
        features=list(features),
        grid=combos,
        taus=tuple(taus),
        folds=results,
        trials=trials,
        predictions=Predictions(**{k: np.concatenate(v) for k, v in pred.items()}),
    )


def replay(
    market: Market,
    horizon: int,
    window: Window,
    idx: np.ndarray,
    p: np.ndarray,
    tau: float | None,
) -> Run:
    """Refaz a simulação de um teste a partir das previsões guardadas (RF-281)."""
    signal = signal_at(len(market.minute_ts_us), idx, p, tau)
    return simulate(market, signal, horizon, window, SCENARIOS)


def _rows(x: np.ndarray, idx: np.ndarray) -> np.ndarray:
    rows = np.asarray(x[idx], dtype=np.float64)
    if not np.isfinite(rows).all():
        raise ValueError("feature indefinida num ponto elegível")
    return rows
