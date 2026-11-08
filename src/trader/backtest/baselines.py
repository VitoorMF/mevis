"""Baselines B0 a B3 (doc 04 §6; RF-231 a RF-234). Sem E/S.

B0 e B3 são sinais, simulados pela regra de operação comum (RF-251). B1 é uma operação por janela.
B2 sorteia, para cada simulação, o mesmo número de operações do modelo em pontos elegíveis, sem
sobreposição, pelo método das posições comprimidas (doc 03 §5.3).
"""

from collections.abc import Sequence

import numpy as np

from trader.backtest.simulator import BASE, Market, Scenario, Window, trade_returns

B2_SIMULATIONS = 1000


def b0_signal(market: Market) -> np.ndarray:
    """Nunca opera (RF-231)."""
    return np.zeros(len(market.minute_ts_us), dtype=bool)


def b3_signal(market: Market) -> np.ndarray:
    """Rompimento de 60 min: fechamento de t acima da máxima de t - 60 a t - 1 (RF-234)."""
    with np.errstate(invalid="ignore"):
        return np.asarray(market.close > market.high_prev_60)  # NaN nunca sinaliza


def b1_trade(market: Market, window: Window) -> tuple[np.ndarray, np.ndarray]:
    """Uma operação na janela (RF-232): decisão no primeiro minuto com preço de entrada e saída no
    preço de execução do último minuto da janela (ou do último que tiver preço)."""
    prices = market.entry_price[window.start : window.stop - 1]  # preço no minuto t + 1 da janela
    priced = np.flatnonzero(np.isfinite(prices))
    if len(priced) < 2:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    decision = window.start + int(priced[0])
    exit_index = window.start + int(priced[-1])
    return np.array([decision], dtype=np.int64), np.array([exit_index], dtype=np.int64)


def candidates(market: Market, horizon: int, window: Window) -> np.ndarray:
    """Pontos elegíveis da janela com a saída dentro dela: onde B2 pode sortear."""
    last = window.stop - 2 - horizon
    if last < window.start:
        return np.zeros(0, dtype=np.int64)
    eligible = market.eligible[horizon][window.start : last + 1]
    return np.flatnonzero(eligible) + window.start


def sample_positions(rng: np.random.Generator, m: int, n: int, horizon: int) -> np.ndarray:
    """N posições numa lista de M pontos, a pelo menos H + 1 uma da outra, com chance igual para
    todas as combinações possíveis: sorteia N posições distintas entre M - (N - 1) · H e soma H
    vezes a ordem de cada uma."""
    slots = m - (n - 1) * horizon
    if n > 0 and slots < n:
        raise ValueError(f"B2: {n} operações sem sobreposição não cabem em {m} pontos")
    chosen = np.sort(rng.choice(slots, size=n, replace=False, shuffle=False))
    return chosen + np.arange(n) * horizon


def b2_totals(
    market: Market,
    horizon: int,
    plan: Sequence[tuple[Window, int]],
    seed: int,
    scenario: Scenario = BASE,
    simulations: int = B2_SIMULATIONS,
) -> np.ndarray:
    """Distribuição do retorno líquido total de B2 (RF-233).

    `plan` traz cada janela com o número de operações do modelo nela; cada simulação sorteia todas
    as janelas e junta os resultados, como nas dobras do desenvolvimento."""
    rng = np.random.Generator(np.random.PCG64(seed))
    log_total = np.zeros(simulations)
    for window, n in plan:
        if n == 0:
            continue
        points = candidates(market, horizon, window)
        returns, _, _ = trade_returns(market, points, points + horizon, scenario)
        log_returns = np.log1p(returns)
        for s in range(simulations):
            log_total[s] += log_returns[sample_positions(rng, len(points), n, horizon)].sum()
    return np.expm1(log_total)
