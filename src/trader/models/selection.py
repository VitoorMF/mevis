"""Escolhas na validação interna (doc 04 §8.2; DEC-41; RF-239 a RF-241). Sem E/S.

Os hiperparâmetros saem pelo log loss, que usa todos os pontos do mês; o τ, pelo Sharpe da operação
simulada no cenário base, entre os τ com pelo menos 20 operações. Sem τ elegível ou com Sharpe que
não é positivo, a estratégia se abstém no mês de teste.
"""

from collections.abc import Sequence

import numpy as np

from trader.backtest.metrics import Metrics

MIN_TRADES = 20
EPS = 1e-15


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    """Log loss médio, com as probabilidades limitadas a [1e-15, 1 - 1e-15]."""
    if len(y) == 0:
        raise ValueError("log loss sem pontos")
    q = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Área sob a curva ROC pela estatística de Mann-Whitney, com empates pela média dos postos;
    `None` se só houver uma classe."""
    positives = int(np.sum(y == 1))
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sorted_p = p[order]
    # Postos médios dos empates: cada grupo de valores iguais recebe a média das posições.
    starts = np.flatnonzero(np.concatenate([[True], sorted_p[1:] != sorted_p[:-1]]))
    ends = np.append(starts[1:], len(p))
    mean_rank = (starts + ends + 1) / 2  # posições de 1 a n
    ranks[order] = np.repeat(mean_rank, ends - starts)
    rank_sum = float(ranks[y == 1].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def choose_hyperparameters(losses: Sequence[float]) -> int:
    """Índice na grade da combinação de menor log loss; num empate, a primeira (RF-239)."""
    if not losses:
        raise ValueError("grade vazia")
    return min(range(len(losses)), key=lambda i: (losses[i], i))


def choose_tau(results: Sequence[tuple[float, Metrics]]) -> float | None:
    """τ de maior Sharpe entre os que têm pelo menos 20 operações; num empate, o maior τ (RF-240).

    `None` é a abstenção: nenhum τ elegível, ou Sharpe do escolhido que não é positivo (RF-241)."""
    eligible = [
        (m.sharpe, tau) for tau, m in results if m.trades >= MIN_TRADES and m.sharpe is not None
    ]
    if not eligible:
        return None
    sharpe, tau = max(eligible)
    return tau if sharpe > 0 else None
