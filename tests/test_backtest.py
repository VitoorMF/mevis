"""Simulador, métricas e baselines (RF-231 a RF-234, RF-251 a RF-255; doc 04 §6, §9, §10.1)."""

import math
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_datasets import build
from test_minutes import ARCHIVE_DAY, build_data

from trader.backtest.baselines import (
    b0_signal,
    b1_trade,
    b2_totals,
    b3_signal,
    candidates,
    sample_positions,
)
from trader.backtest.metrics import ANNUALIZATION, metrics
from trader.backtest.simulator import (
    BASE,
    DAY_US,
    REPORTED,
    SCENARIOS,
    Market,
    Scenario,
    Window,
    chain,
    run_trades,
    select_trades,
    simulate,
)
from trader.core.timeunits import MINUTE_US
from trader.datasets.labels import FEE, SLIPPAGE

DAY = date(2026, 3, 2)
DAY0_US = int((DAY - date(1970, 1, 1)).days) * DAY_US


def make_market(
    days: int = 1,
    price: float = 100.0,
    horizons: tuple[int, ...] = (5,),
    **columns: np.ndarray,
) -> Market:
    n = days * 1440
    flat = np.full(n, price)
    return Market(
        minute_ts_us=DAY0_US + np.arange(n, dtype=np.int64) * MINUTE_US,
        entry_price=columns.get("entry_price", flat.copy()),
        entry_price_stress=columns.get("entry_price_stress", flat.copy()),
        close=columns.get("close", flat.copy()),
        high_prev_60=columns.get("high_prev_60", np.full(n, np.nan)),
        eligible={h: columns.get(f"eligible_h{h}", np.ones(n, bool)) for h in horizons},
    )


def net(entry: float, exit_price: float, scenario: Scenario = BASE) -> float:
    return exit_price * scenario.keep / (entry * scenario.pay) - 1


# ------------------------------------------------------------------ cenários (RF-253)


def test_scenarios_follow_doc_04() -> None:
    by_name = {s.name: s for s in SCENARIOS}
    assert len(by_name) == 7 and set(REPORTED) <= set(by_name)
    assert (BASE.fee, BASE.slippage, BASE.stress_latency) == (FEE, SLIPPAGE, False)
    stress = by_name["estresse"]
    assert (stress.fee, stress.slippage, stress.stress_latency) == (0.0015, 0.0003, True)
    assert 1 - stress.keep / stress.pay == pytest.approx(0.0036, abs=1e-4), "~0,36% por operação"
    assert by_name["latencia_2s"].fee == FEE and by_name["latencia_2s"].stress_latency


# ------------------------------------------------------------------ regra de operação (RF-251)


def test_no_overlap_and_next_signal() -> None:
    market = make_market()
    window = market.full_window()
    decisions = select_trades(np.ones(1440, bool), market.eligible[5], 5, window)
    assert decisions[:3].tolist() == [0, 6, 12], "próximo sinal em t + H + 1"
    assert decisions[-1] <= 1440 - 2 - 5, "a saída cai na janela"
    sparse = np.zeros(1440, bool)
    sparse[[10, 13, 15, 16, 30]] = True
    assert select_trades(sparse, market.eligible[5], 5, window).tolist() == [10, 16, 30]


def test_only_eligible_points_inside_the_window() -> None:
    eligible = np.ones(2880, bool)
    eligible[100] = False
    market = make_market(days=2, eligible_h5=eligible)
    signal = np.zeros(2880, bool)
    signal[[100, 1432, 1433, 1500]] = True
    first_day = market.window(DAY, DAY)
    assert select_trades(signal, eligible, 5, first_day).tolist() == [1432]
    second_day = market.window(DAY + timedelta(days=1), DAY + timedelta(days=1))
    assert second_day == Window(1440, 2880, DAY + timedelta(days=1), DAY + timedelta(days=1))
    assert select_trades(signal, eligible, 5, second_day).tolist() == [1500]
    with pytest.raises(ValueError, match="janela fora do dataset"):
        market.window(DAY, DAY + timedelta(days=2))


def test_trade_return_equals_the_label_in_the_base_scenario(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    table = pq.read_table(build(root, tmp_path / "out").path)
    market = Market.from_table(table)
    window = market.window(ARCHIVE_DAY, ARCHIVE_DAY)
    run = simulate(market, np.ones(len(market.minute_ts_us), bool), 15, window)
    assert len(run.decisions) == 4, "só depois dos 1.500 minutos sem histórico"
    labels = table["net_return_h15"].to_numpy(zero_copy_only=False)
    assert np.array_equal(run.paths["base"].returns, labels[run.decisions]), "RF-252"
    valid = table["target_valid_h15"].to_numpy(zero_copy_only=False)
    decisions = select_trades(valid, valid, 15, window)
    assert len(decisions) > 50
    every = run_trades(market, decisions, decisions + 15, window, 15)
    assert np.array_equal(every.paths["base"].returns, labels[decisions]), "RF-252"
    assert set(run.paths) == {s.name for s in SCENARIOS}
    for path in run.paths.values():
        assert len(path.returns) == len(run.decisions), "as mesmas operações em todos os cenários"
    with pytest.raises(ValueError, match="minutos contíguos"):
        Market.from_table(table.slice(1))


# ------------------------------------------------------------ capital e métricas (RF-254, RF-255)


def test_equity_is_marked_every_minute_by_liquidation_value() -> None:
    close = np.full(1440, 100.0)
    close[[11, 12, 13]] = [95.0, np.nan, 103.0]
    entry_price = np.full(1440, 100.0)
    entry_price[15] = 104.0  # saída da decisão em t = 10, no minuto 16
    market = make_market(close=close, entry_price=entry_price)
    window = market.full_window()
    run = run_trades(market, np.array([10]), np.array([15]), window, 5, [BASE])
    path = run.paths["base"]
    factor = BASE.keep / (100.0 * BASE.pay)
    r = net(100.0, 104.0)
    assert path.returns.tolist() == [pytest.approx(r)]
    assert path.equity[10] == 1.0, "o minuto da decisão ainda está fora"
    assert path.equity[11] == pytest.approx(95 * factor)
    assert path.equity[12] == path.equity[11], "sem fechamento, repete a última marca"
    assert path.equity[13] == pytest.approx(103 * factor)
    assert path.equity[15] == pytest.approx(100 * factor), "último minuto com posição"
    assert path.equity[16] == pytest.approx(1 + r) and path.equity[-1] == path.equity[16]
    result = metrics(path)
    assert result.max_drawdown == pytest.approx(1 - 95 * factor)
    assert result.total_return == pytest.approx(r)
    assert result.exposure == pytest.approx(5 / 1440)
    assert (result.trades, result.hit_rate) == (1, 1.0)
    assert result.expectancy_bps == pytest.approx(r * 1e4)


def test_first_minute_without_close_keeps_the_capital_before_entry() -> None:
    close = np.full(1440, 100.0)
    close[21] = np.nan
    market = make_market(close=close)
    decisions = np.array([0, 20])
    run = run_trades(market, decisions, decisions + 5, market.full_window(), 5, [BASE])
    equity = run.paths["base"].equity
    after_first = 1 + net(100, 100)
    assert equity[21] == pytest.approx(after_first), "repete o capital antes da entrada"
    assert equity[22] == pytest.approx(after_first * 100 * BASE.keep / (100 * BASE.pay))


def test_sharpe_uses_every_day_of_the_window() -> None:
    entry_price = np.full(3 * 1440, 100.0)
    entry_price[1445] = 103.0  # operação no segundo dia
    market = make_market(days=3, entry_price=entry_price)
    window = market.full_window()
    run = run_trades(market, np.array([1440]), np.array([1445]), window, 5, [BASE])
    result = metrics(run.paths["base"])
    daily = np.array([0.0, net(100, 103), 0.0])
    expected = daily.mean() / daily.std(ddof=1) * ANNUALIZATION
    assert result.days == 3 and result.sharpe == pytest.approx(expected)
    assert math.isclose(ANNUALIZATION, math.sqrt(365))


def test_stress_latency_uses_the_4s_price_with_fallback() -> None:
    stress_price = np.full(1440, 101.0)
    stress_price[3] = np.nan
    market = make_market(entry_price_stress=stress_price)
    decisions = np.array([0, 10])
    run = run_trades(market, decisions, decisions + 3, market.full_window(), 3)
    latency = run.paths["latencia_2s"]
    by_name = {s.name: s for s in SCENARIOS}
    assert latency.returns[0] == pytest.approx(net(101, 100, by_name["latencia_2s"]))
    assert latency.returns[1] == pytest.approx(net(101, 101, by_name["latencia_2s"]))
    assert latency.fallbacks == 1 and metrics(latency).stress_fallbacks == 1
    assert run.paths["base"].fallbacks == 0
    assert run.paths["estresse"].returns[1] < run.paths["latencia_2s"].returns[1] < 0


def test_overlapping_or_outside_trades_are_refused() -> None:
    market = make_market()
    window = market.full_window()
    with pytest.raises(ValueError, match="sobrepostas"):
        run_trades(market, np.array([0, 3]), np.array([5, 8]), window, 5)
    with pytest.raises(ValueError, match="fora da janela"):
        run_trades(market, np.array([1435]), np.array([1439]), window, 5)


def test_chain_continues_the_capital() -> None:
    entry_price = np.full(2880, 100.0)
    entry_price[[5, 1445]] = [102.0, 99.0]
    market = make_market(days=2, entry_price=entry_price)
    days = [market.window(DAY + timedelta(days=i), DAY + timedelta(days=i)) for i in range(2)]
    runs = [
        run_trades(market, np.array([w.start]), np.array([w.start + 5]), w, 5, [BASE]) for w in days
    ]
    joined = chain([run.paths["base"] for run in runs])
    whole = run_trades(
        market, np.array([0, 1440]), np.array([5, 1445]), market.full_window(), 5, [BASE]
    )
    assert np.allclose(joined.equity, whole.paths["base"].equity)
    assert metrics(joined) == metrics(whole.paths["base"])


# ------------------------------------------------------------------ baselines (RF-231 a RF-234)


def test_b0_never_trades() -> None:
    market = make_market(days=2)
    result = metrics(simulate(market, b0_signal(market), 5, market.full_window()).paths["base"])
    assert (result.trades, result.total_return, result.max_drawdown) == (0, 0.0, 0.0)
    assert (result.expectancy_bps, result.sharpe, result.hit_rate) == (0.0, None, None)


def test_b1_holds_the_whole_window() -> None:
    entry_price = np.linspace(100, 110, 1440)
    entry_price[[0, 1438]] = np.nan
    market = make_market(entry_price=entry_price)
    window = market.full_window()
    decisions, exits = b1_trade(market, window)
    assert decisions.tolist() == [1] and exits.tolist() == [1437]
    run = run_trades(market, decisions, exits, window, 5, [BASE])
    assert metrics(run.paths["base"]).total_return == pytest.approx(
        net(entry_price[1], entry_price[1437])
    )


def test_b3_signals_breakouts_only() -> None:
    close = np.array([10.0, 12.0, 11.0, 13.0] * 360)
    high = np.array([11.0, 11.0, np.nan, 13.0] * 360)
    market = make_market(close=close, high_prev_60=high)
    assert b3_signal(market)[:4].tolist() == [False, True, False, False]


def test_b2_positions_never_overlap_and_are_reproducible() -> None:
    rng = np.random.default_rng(1)
    for _ in range(200):
        positions = sample_positions(rng, 100, 6, 15)
        assert (np.diff(positions) >= 16).all() and positions[-1] < 100
    eligible = np.ones(1440, bool)
    eligible[:300] = False
    market = make_market(eligible_h5=eligible, entry_price=np.linspace(100, 101, 1440))
    window = market.full_window()
    points = candidates(market, 5, window)
    assert points[0] == 300 and points[-1] == 1440 - 2 - 5
    first = b2_totals(market, 5, [(window, 20)], seed=7, simulations=50)
    assert np.array_equal(first, b2_totals(market, 5, [(window, 20)], seed=7, simulations=50))
    assert not np.array_equal(first, b2_totals(market, 5, [(window, 20)], seed=8, simulations=50))
    assert np.array_equal(b2_totals(market, 5, [(window, 0)], seed=7, simulations=5), np.zeros(5))
    with pytest.raises(ValueError, match="não cabem"):
        b2_totals(market, 5, [(window, 300)], seed=7, simulations=1)


def test_b2_draws_every_combination_with_the_same_chance() -> None:
    m, n, h = 7, 2, 2
    allowed = [c for c in combinations(range(m), n) if c[1] - c[0] >= h + 1]
    rng = np.random.default_rng(3)
    draws = 30_000
    counts = dict.fromkeys(allowed, 0)
    for _ in range(draws):
        counts[tuple(int(x) for x in sample_positions(rng, m, n, h))] += 1
    expected = draws / len(allowed)
    assert all(abs(c - expected) < 5 * math.sqrt(expected) for c in counts.values()), counts


def test_market_needs_whole_days() -> None:
    table = pa.table({"minute_ts_us": pa.array([DAY0_US + MINUTE_US], pa.int64())})
    with pytest.raises(ValueError, match="minutos contíguos"):
        Market.from_table(table)
