"""Bootstrap em blocos de um dia e Deflated Sharpe Ratio (RF-277 a RF-279; doc 04 §10.1)."""

import math

import numpy as np
import pytest
from scipy import stats

from trader.backtest.metrics import ANNUALIZATION
from trader.stats.bootstrap import (
    RESAMPLES,
    Days,
    draws,
    expectancy_interval,
    expectancy_samples,
    paired,
    sharpe_samples,
)
from trader.stats.dsr import EULER, deflated_sharpe, expected_max_sharpe, trial_variance


def days_with(daily: list[float], trades: dict[int, list[float]]) -> Days:
    returns = [r for day in sorted(trades) for r in trades[day]]
    positions = [day for day in sorted(trades) for _ in trades[day]]
    return Days(np.array(daily), np.array(returns), np.array(positions, dtype=np.int64))


# ------------------------------------------------------------------ bootstrap (RF-277, RF-279)


def test_constant_returns_give_a_degenerate_interval() -> None:
    days = days_with([0.001] * 20, {d: [0.002, 0.002] for d in range(20)})
    ci = expectancy_interval(days, seed=0)
    assert ci.estimate == pytest.approx(0.002)
    assert ci.low == pytest.approx(0.002) and ci.high == pytest.approx(0.002)
    assert (ci.resamples, ci.empty) == (RESAMPLES, 0)
    flat = days_with([0.0] * 20, {})
    sharpe = sharpe_samples(flat, draws(20, 0, 100))
    assert np.isnan(sharpe).all(), "dias sem variação não têm Sharpe"


def test_samples_without_trades_are_left_out_and_counted() -> None:
    days = days_with([0.0] * 9 + [0.01], {9: [0.01]})
    idx = draws(10, seed=5)
    expected_empty = int((~(idx == 9).any(axis=1)).sum())
    ci = expectancy_interval(days, seed=5)
    assert ci.empty == expected_empty and 0 < ci.empty < RESAMPLES
    assert ci.empty / RESAMPLES == pytest.approx(0.9**10, abs=0.02)
    assert ci.low == pytest.approx(0.01) and ci.high == pytest.approx(0.01)
    assert ci.to_dict()["empty"] == expected_empty


def test_expectancy_weights_each_trade_by_how_often_its_day_is_drawn() -> None:
    days = days_with([0.0, 0.0, 0.0], {0: [0.01, 0.03], 2: [-0.02]})
    idx = np.array([[0, 0, 1], [2, 2, 2], [1, 1, 1]])
    got = expectancy_samples(days, idx)
    assert got[0] == pytest.approx(0.02) and got[1] == pytest.approx(-0.02)
    assert np.isnan(got[2])


def test_same_seed_same_interval_and_paired_uses_the_same_days() -> None:
    rng = np.random.default_rng(2)
    a = Days(rng.normal(0, 0.01, 30), rng.normal(0, 0.01, 60), np.repeat(np.arange(30), 2))
    b = Days(rng.normal(0, 0.01, 30), rng.normal(0, 0.01, 30), np.arange(30))
    assert expectancy_interval(a, 7) == expectancy_interval(a, 7)
    de, ds = paired(a, a, seed=3)
    assert (de.low, de.high, ds.low, ds.high) == (0.0, 0.0, 0.0, 0.0)
    de, ds = paired(a, b, seed=3)
    idx = draws(30, 3)
    diff = expectancy_samples(a, idx) - expectancy_samples(b, idx)
    assert de.low == pytest.approx(np.percentile(diff, 2.5))
    assert de.estimate == pytest.approx(a.trade_returns.mean() - b.trade_returns.mean())
    assert ds.estimate == pytest.approx(annualized(a.daily) - annualized(b.daily))
    with pytest.raises(ValueError, match="mesmos dias"):
        paired(a, Days(np.zeros(29), np.zeros(0), np.zeros(0, dtype=np.int64)), seed=0)


def annualized(daily: np.ndarray) -> float:
    return float(daily.mean() / daily.std(ddof=1) * ANNUALIZATION)


def test_days_are_checked() -> None:
    with pytest.raises(ValueError, match="dia da saída"):
        Days(np.zeros(3), np.zeros(2), np.zeros(1, dtype=np.int64))
    with pytest.raises(ValueError, match="fora"):
        Days(np.zeros(3), np.zeros(1), np.array([3]))
    with pytest.raises(ValueError, match="sem dias"):
        draws(0, 0)


# ------------------------------------------------------------------ DSR (RF-278)


def reference(daily: np.ndarray, trials: int, variance: float) -> tuple[float, float]:
    """A fórmula do doc 04 §10.1 com as funções do scipy."""
    sr = daily.mean() / daily.std(ddof=1)
    sr0 = math.sqrt(variance) * (
        (1 - EULER) * stats.norm.ppf(1 - 1 / trials)
        + EULER * stats.norm.ppf(1 - 1 / (trials * math.e))
    )
    skew = stats.skew(daily, bias=True)
    kurt = stats.kurtosis(daily, fisher=False, bias=True)
    z = (sr - sr0) * math.sqrt(len(daily) - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    return sr0, float(stats.norm.cdf(z))


def test_dsr_matches_the_formula() -> None:
    rng = np.random.default_rng(4)
    daily = rng.standard_t(5, 180) * 0.01 + 0.001
    got = deflated_sharpe(daily, trials=180, variance=0.004)
    sr0, dsr = reference(daily, 180, 0.004)
    assert got.expected_max == pytest.approx(sr0)
    assert got.dsr == pytest.approx(dsr)
    assert got.sharpe == pytest.approx(daily.mean() / daily.std(ddof=1))
    assert (got.days, got.trials) == (180, 180)
    assert got.kurtosis == pytest.approx(stats.kurtosis(daily, fisher=False, bias=True))


def test_dsr_by_hand() -> None:
    """Quatro dias, N = 10, V = 0,01: SR = 0,005 / 0,01291 = 0,3873, assimetria 0, curtose 1,64;
    SR0 = 0,1 · (0,42278 · 1,28155 + 0,57722 · 1,78924) = 0,15746;
    z = (0,3873 - 0,15746) · raiz(3) / raiz(1 + 0,64 / 4 · 0,15) = 0,3934; DSR = Φ(z) = 0,6530."""
    got = deflated_sharpe(np.array([0.01, -0.01, 0.02, 0.0]), 10, 0.01)
    assert got.sharpe == pytest.approx(0.3873, abs=1e-4)
    assert got.skewness == pytest.approx(0.0, abs=1e-12)
    assert got.kurtosis == pytest.approx(1.64)
    assert got.expected_max == pytest.approx(0.15746, abs=1e-5)
    assert got.dsr == pytest.approx(0.6530, abs=2e-4)


def test_dsr_edge_cases() -> None:
    assert expected_max_sharpe(0.01, 1) == 0.0
    flat = deflated_sharpe(np.zeros(30), trials=5, variance=0.01)
    assert flat.dsr is None and flat.sharpe == 0.0
    assert trial_variance([0.1, None, 0.3]) == pytest.approx(np.var([0.1, 0.0, 0.3], ddof=1))
    assert trial_variance([0.2]) == 0.0
    single = deflated_sharpe(np.array([0.01, 0.02, 0.0]), trials=1, variance=0.0)
    assert single.expected_max == 0.0 and single.dsr is not None
