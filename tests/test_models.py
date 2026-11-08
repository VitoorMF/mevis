"""Dobras, seleção, modelos e walk-forward da etapa 6 (RF-235 a RF-244; doc 04 §7 e §8)."""

import json
from datetime import date, timedelta

import numpy as np
import pytest
from test_backtest import DAY, make_market

from trader.backtest.metrics import Metrics, metrics
from trader.backtest.simulator import BASE, SCENARIOS, Market, chain
from trader.features.compute import FEATURES
from trader.models.estimators import GRIDS, LOGISTIC, TAUS, XGBOOST, fit
from trader.models.folds import folds, months, points
from trader.models.selection import MIN_TRADES, auc, choose_hyperparameters, choose_tau, log_loss
from trader.models.walkforward import (
    TEST,
    VALIDATION,
    Task,
    replay,
    rung_features,
    signal_at,
    walk_forward,
)

H = 5
"""Horizonte dos casos sintéticos (as colunas de `make_market` se chamam `eligible_h5`)."""

# ------------------------------------------------------------------ degraus (RF-235)


def test_rungs_take_the_catalog_families() -> None:
    family = dict(FEATURES)
    b4, b5, b6 = (rung_features(r) for r in ("B4", "B5", "B6"))
    assert (len(b4), len(b5), len(b6)) == (18, 25, 37)
    assert {family[n] for n in b4} == {"candles"}
    assert b5[: len(b4)] == b4 and b6[: len(b5)] == b5
    assert {family[n] for n in b6} == {"candles", "volume", "flow"}
    assert not any(n.startswith(("target_", "eligible_", "entry_price")) for n in b6)
    with pytest.raises(ValueError, match="degrau"):
        rung_features("B7")


# ------------------------------------------------------------------ dobras e purga (RF-236, RF-238)


def test_months_run_from_the_23rd_to_the_22nd() -> None:
    periods = months(date(2025, 10, 23), date(2026, 7, 22))
    assert len(periods) == 9
    assert periods[0] == (date(2025, 10, 23), date(2025, 11, 22))
    assert periods[2] == (date(2025, 12, 23), date(2026, 1, 22))
    assert periods[-1] == (date(2026, 6, 23), date(2026, 7, 22))
    with pytest.raises(ValueError, match="dia 23"):
        months(date(2025, 10, 22), date(2026, 7, 22))
    with pytest.raises(ValueError, match="dia 23"):
        months(date(2025, 10, 23), date(2026, 7, 23))


def test_track_a_folds_match_doc_04() -> None:
    first, last = date(2025, 10, 23), date(2026, 7, 22)
    days = (last - first).days + 1
    start_us = (first - date(1970, 1, 1)).days * 86_400_000_000
    n = days * 1440
    market = Market(
        minute_ts_us=start_us + np.arange(n, dtype=np.int64) * 60_000_000,
        entry_price=np.ones(n),
        entry_price_stress=np.ones(n),
        close=np.ones(n),
        high_prev_60=np.ones(n),
        eligible={15: np.ones(n, bool)},
    )
    got = [f.labels() for f in folds(market, months(first, last))]
    assert [f["test"] for f in got] == [
        "2026-01-23..2026-02-22",
        "2026-02-23..2026-03-22",
        "2026-03-23..2026-04-22",
        "2026-04-23..2026-05-22",
        "2026-05-23..2026-06-22",
        "2026-06-23..2026-07-22",
    ]
    assert got[0] == {
        "fit": "2025-10-23..2025-12-22",
        "validation": "2025-12-23..2026-01-22",
        "train": "2025-10-23..2026-01-22",
        "test": "2026-01-23..2026-02-22",
    }
    assert got[-1] == {
        "fit": "2025-10-23..2026-05-22",
        "validation": "2026-05-23..2026-06-22",
        "train": "2025-10-23..2026-06-22",
        "test": "2026-06-23..2026-07-22",
    }
    with pytest.raises(ValueError, match="períodos"):
        folds(market, months(first, date(2026, 1, 22)))


def test_purge_drops_points_whose_exit_leaves_the_window() -> None:
    eligible = np.ones(2 * 1440, bool)
    eligible[100] = False
    market = make_market(days=2, horizons=(H,), eligible_h5=eligible)
    first = market.window(DAY, DAY)
    idx = points(market, H, first)
    assert idx[0] == 0 and 100 not in idx
    assert idx[-1] == first.stop - 2 - H, "a saída, em t + 1 + H, é o último minuto da janela"
    assert len(idx) == first.stop - 1 - H - 1
    second = market.window(DAY + timedelta(days=1), DAY + timedelta(days=1))
    assert points(market, H, second)[0] == second.start


# ------------------------------------------------------------------ seleção (RF-239 a RF-241)


def m(trades: int, sharpe: float | None) -> Metrics:
    return Metrics(trades, 0.0, 0.0, sharpe, 0.0, None, 0.0, 30, 0)


def test_hyperparameters_by_log_loss_with_ties_to_the_first() -> None:
    assert choose_hyperparameters([0.5, 0.4, 0.45]) == 1
    assert choose_hyperparameters([0.4, 0.5, 0.4]) == 0
    with pytest.raises(ValueError):
        choose_hyperparameters([])


def test_tau_by_sharpe_with_at_least_20_trades_and_ties_to_the_larger() -> None:
    results = [(0.5, m(100, 1.0)), (0.55, m(60, 2.0)), (0.6, m(19, 9.0)), (0.65, m(25, 2.0))]
    assert choose_tau(results) == 0.65, "empate de Sharpe: o maior τ; 19 operações não contam"
    assert choose_tau([(0.5, m(30, None)), (0.55, m(20, 0.5))]) == 0.55
    assert MIN_TRADES == 20


def test_abstention_without_trades_or_with_sharpe_that_is_not_positive() -> None:
    assert choose_tau([(0.5, m(19, 3.0)), (0.55, m(0, None))]) is None
    assert choose_tau([(0.5, m(50, -0.2)), (0.55, m(40, -0.1))]) is None
    assert choose_tau([(0.5, m(50, 0.0))]) is None


def test_log_loss_and_auc() -> None:
    y = np.array([0.0, 0.0, 1.0, 1.0])
    p = np.array([0.1, 0.4, 0.35, 0.8])
    expected = -np.mean(np.log([0.9, 0.6, 0.35, 0.8]))
    assert log_loss(y, p) == pytest.approx(expected)
    assert np.isfinite(log_loss(y, np.array([0.0, 0.0, 1.0, 1.0])))
    assert auc(y, p) == pytest.approx(0.75)
    assert auc(y, np.full(4, 0.3)) == pytest.approx(0.5), "empates valem meio"
    assert auc(np.ones(3), np.array([0.1, 0.2, 0.3])) is None


# ------------------------------------------------------------------ modelos (RF-237, RF-244)


def blobs(n: int = 2000, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.3).astype(float)
    x = np.column_stack([y * 2 + rng.normal(size=n), rng.normal(size=n) * 50 + 1000])
    return x, y


@pytest.mark.parametrize("model", [LOGISTIC, XGBOOST])
def test_same_inputs_same_predictions(model: str) -> None:
    x, y = blobs()
    params = GRIDS[model][0]
    first = fit(model, params, x, y).predict(x[:200])
    again = fit(model, params, x, y).predict(x[:200])
    assert first.dtype == np.float64 and np.array_equal(first, again)
    importance = fit(model, params, x, y).importance()
    assert importance.shape == (2,) and importance[0] > importance[1]


def test_logistic_standardizes_with_the_fit_rows_only() -> None:
    x, y = blobs()
    model = fit(LOGISTIC, {"C": 1.0}, x, y)
    assert np.allclose(model.mean, x.mean(axis=0)) and np.allclose(model.scale, x.std(axis=0))  # type: ignore[attr-defined]
    shifted = fit(LOGISTIC, {"C": 1.0}, x * [1, 10] + [5, -3], y)
    assert np.allclose(model.predict(x), shifted.predict(x * [1, 10] + [5, -3]), atol=1e-6)
    constant = np.column_stack([x[:, 0], np.ones(len(x))])
    assert np.isfinite(fit(LOGISTIC, {"C": 1.0}, constant, y).predict(constant)).all()


def test_fit_needs_both_classes() -> None:
    x, _ = blobs()
    with pytest.raises(ValueError, match="classes"):
        fit(LOGISTIC, {"C": 1.0}, x, np.zeros(len(x)))
    with pytest.raises(ValueError, match="modelo"):
        fit("svm", {}, x, np.arange(len(x)) % 2)


# ------------------------------------------------------------------ walk-forward (RF-238 a RF-244)


def synthetic(
    days: int = 10, seed: int = 3, signal: float = 1.0
) -> tuple[Market, np.ndarray, np.ndarray]:
    """Preço em passeio aleatório; a primeira feature antecipa o retorno da operação."""
    rng = np.random.default_rng(seed)
    n = days * 1440
    price = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    ahead = np.full(n, np.nan)
    ahead[:-H] = np.log(price[H:] / price[:-H])
    target = np.where(np.isnan(ahead), np.nan, (np.exp(ahead) * BASE.keep / BASE.pay - 1 > 0) * 1.0)
    eligible = ~np.isnan(target)
    lead = np.where(eligible, np.nan_to_num(ahead) * 1000 * signal, 0) + rng.normal(0, 1, n)
    x = np.column_stack([lead, rng.normal(size=n)])
    market = make_market(
        days=days,
        horizons=(H,),
        entry_price=price,
        entry_price_stress=price * 1.0001,
        close=price,
        eligible_h5=eligible,
    )
    return market, x, target


def periods(count: int = 5, length: int = 2) -> list[tuple[date, date]]:
    return [
        (DAY + timedelta(days=k * length), DAY + timedelta(days=k * length + length - 1))
        for k in range(count)
    ]


def run(market: Market, x: np.ndarray, target: np.ndarray, **kwargs: object):  # type: ignore[no-untyped-def]
    task = Task("B4", H, LOGISTIC)
    return walk_forward(market, x, target, task, folds(market, periods()), ["a", "b"], **kwargs)  # type: ignore[arg-type]


def test_walk_forward_selects_trades_and_records_everything() -> None:
    market, x, target = synthetic()
    result = run(market, x, target)
    assert [f.fold.name for f in result.folds] == ["D1", "D2"]
    assert len(result.trials) == len(GRIDS[LOGISTIC]) * len(TAUS)
    for f in result.folds:
        assert not f.abstained and f.tau in TAUS
        assert len(f.losses) == 2 and f.chosen == choose_hyperparameters(f.losses)
        assert f.tau == choose_tau(f.taus)
        assert set(f.test.paths) == {s.name for s in SCENARIOS}
        assert len(f.test.paths["base"].returns) > 0 and f.test_auc is not None
        assert f.test_auc > 0.7
    trial = result.trials[0]
    labels = [f.fold.validation.label for f in result.folds]
    assert list(trial.metrics) == [*labels, "validation"]
    assert set(trial.metrics[labels[0]]["base"]) >= {"trades", "sharpe", "log_loss", "auc"}
    outcome = json.loads(json.dumps(result.outcome()))
    assert outcome["folds"][0]["windows"]["test"] == result.folds[0].fold.test.label
    joined = chain([f.test.paths["base"] for f in result.folds])
    assert outcome["combined"]["base"] == metrics(joined).to_dict()
    p = result.predictions
    assert set(np.unique(p.segment)) == {VALIDATION, TEST}
    for k, f in enumerate(result.folds):
        rows = (p.segment == TEST) & (p.fold == k)
        assert np.all(p.combo[rows] == f.chosen)
        again = replay(market, H, f.fold.test, p.minute[rows], p.p[rows], f.tau)
        assert metrics(again.paths["base"]) == metrics(f.test.paths["base"])


def test_test_month_does_not_change_the_choices() -> None:
    market, x, target = synthetic()
    first = run(market, x, target)
    last = first.folds[-1].fold.test
    span = slice(last.start, last.stop)
    rng = np.random.default_rng(9)
    target2 = target.copy()
    target2[span] = np.where(np.isnan(target[span]), np.nan, 1 - target[span])
    price = market.entry_price.copy()
    price[span] *= np.exp(np.cumsum(rng.normal(0, 0.002, last.stop - last.start)))
    changed = Market(
        minute_ts_us=market.minute_ts_us,
        entry_price=price,
        entry_price_stress=price,
        close=price,
        high_prev_60=market.high_prev_60,
        eligible=market.eligible,
    )
    second = run(changed, x, target2)
    for a, b in zip(first.folds, second.folds, strict=True):
        assert (a.losses, a.chosen, a.tau, a.taus) == (b.losses, b.chosen, b.tau, b.taus)
    assert first.folds[-1].test_log_loss != second.folds[-1].test_log_loss


def test_same_inputs_same_walk_forward() -> None:
    market, x, target = synthetic()
    a, b = run(market, x, target), run(market, x, target)
    assert np.array_equal(a.predictions.p, b.predictions.p)
    assert a.outcome()["combined"] == b.outcome()["combined"]


def test_abstention_leaves_the_test_month_flat() -> None:
    market, x, target = synthetic()
    result = run(market, x, target, taus=(0.999,))
    for f in result.folds:
        assert f.abstained and f.tau is None
        path = f.test.paths["base"]
        assert len(path.returns) == 0 and np.all(path.equity == 1.0)
        assert f.test_auc is not None, "o teste é previsto mesmo na abstenção"
    assert result.outcome()["folds"][0]["abstained"] is True
    assert not signal_at(10, np.arange(3), np.ones(3), None).any()


def test_walk_forward_checks_its_inputs() -> None:
    market, x, target = synthetic()
    with pytest.raises(ValueError, match="matriz"):
        run(market, x[:, :1], target)
    bad = x.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="indefinida"):
        run(market, bad, target)
