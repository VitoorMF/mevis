"""Cálculo das features point-in-time (doc 04 §5.3; RF-201, RF-205 a RF-212)."""

import hashlib
import io
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trader.aggregate.schema import BINS
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.features.compute import (
    DECISION_DELAY_US,
    FAMILIES,
    FEATURES,
    LOOKBACK_MINUTES,
    FeatureFrame,
    catalog_id,
    compute_features,
)
from trader.features.series import (
    BOOK_FIELDS,
    MINUTES,
    Channel,
    MinuteSeries,
    series_from_table,
)

T0 = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp()) * SECOND_US  # segunda-feira, 00:00 UTC
N = 1620
T = N - 1


def make_series(n: int = N, seed: int = 0) -> MinuteSeries:
    rng = np.random.default_rng(seed)
    ts = T0 + np.arange(n, dtype=np.int64) * MINUTE_US
    end = ts + MINUTE_US
    close = 60_000 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    candles = Channel(
        {
            "high": close * (1 + rng.uniform(0, 2e-3, n)),
            "low": close * (1 - rng.uniform(0, 2e-3, n)),
            "close": close,
            "volume": rng.uniform(1, 20, n),
            "trade_count": rng.integers(50, 2000, n).astype(np.float64),
        },
        np.ones(n, dtype=bool),
        end + rng.integers(100_000, 1_500_000, n),
    )
    bins = np.arange(BINS)
    counts = rng.poisson(5 * np.exp(-(((bins - 50) / 8) ** 2)), (n, BINS)).astype(np.int64)
    sizes = 2.0 ** ((bins - 68) / 4) * rng.uniform(1, 1.19, (n, BINS))
    bin_volume = counts * sizes
    volume = bin_volume.sum(axis=1)
    trades = Channel(
        {
            "volume": volume,
            "buy_volume": volume * rng.uniform(0.2, 0.8, n),
            "quote_volume": volume * close * (1 + rng.normal(0, 1e-4, n)),
            "close": close * (1 + rng.normal(0, 1e-4, n)),
            "size_bins_count": counts,
            "size_bins_volume": bin_volume,
        },
        np.ones(n, dtype=bool),
        end + rng.integers(50_000, 1_900_000, n),
    )
    book = Channel(
        {name: rng.uniform(0.01, 1, n) for name in BOOK_FIELDS},
        np.ones(n, dtype=bool),
        end.copy(),
    )
    return MinuteSeries(ts, candles, trades, book)


def changed(
    series: MinuteSeries,
    channel: str,
    *,
    invalid: Sequence[int] = (),
    available: dict[int, int] | None = None,
    values: dict[str, dict[int, float]] | None = None,
) -> MinuteSeries:
    """Cópia da série com minutos inválidos, horários de disponibilidade ou valores trocados."""
    old: Channel = getattr(series, channel)
    valid = old.valid.copy()
    valid[list(invalid)] = False
    ready = old.available_us.copy()
    for index, ts in (available or {}).items():
        ready[index] = ts
    new_values = {k: v.copy() for k, v in old.values.items()}
    for name, by_index in (values or {}).items():
        for index, value in by_index.items():
            new_values[name][index] = value
    return with_channels(series, {channel: Channel(new_values, valid, ready)})


def with_channels(series: MinuteSeries, new: dict[str, Channel]) -> MinuteSeries:
    channels = {"candles": series.candles, "trades": series.trades, "book": series.book} | new
    return MinuteSeries(
        series.minute_ts_us, channels["candles"], channels["trades"], channels["book"]
    )


def end_of(index: int) -> int:
    return T0 + (index + 1) * MINUTE_US


# ------------------------------------------------------------------------------ referência


def reference(series: MinuteSeries, t: int) -> dict[str, float]:
    """As features em t pela letra de doc 04 §5.3, minuto a minuto, com todos os minutos válidos."""
    cv, tv, bv = series.candles.values, series.trades.values, series.book.values
    c, h, low, v, n = (cv[k] for k in ("close", "high", "low", "volume", "trade_count"))
    big_v, big_b, big_q, big_c = (tv[k] for k in ("volume", "buy_volume", "quote_volume", "close"))
    counts, bin_volume = tv["size_bins_count"], tv["size_bins_volume"]

    def window(w: int) -> range:
        return range(t - w + 1, t + 1)

    def r(i: int) -> float:
        return math.log(c[i] / c[i - 1])

    out: dict[str, float] = {}
    for w in (1, 5, 15, 60, 240):
        out[f"ret_{w}"] = math.log(c[t] / c[t - w])
    for w in (15, 60):
        out[f"rvol_{w}"] = math.sqrt(sum(r(i) ** 2 for i in window(w)))
    for w in (1, 15):
        out[f"range_{w}"] = (max(h[i] for i in window(w)) - min(low[i] for i in window(w))) / c[t]
    for w in (15, 60, 240):
        out[f"sma_dist_{w}"] = c[t] / (sum(c[i] for i in window(w)) / w) - 1
    gains = sum(max(c[i] - c[i - 1], 0.0) for i in window(14))
    losses = sum(max(c[i - 1] - c[i], 0.0) for i in window(14))
    out["rsi_14"] = 100 * gains / (gains + losses)
    lowest, highest = min(low[i] for i in window(60)), max(h[i] for i in window(60))
    out["range_pos_60"] = (c[t] - lowest) / (highest - lowest)
    moment = datetime.fromtimestamp(end_of(t) / SECOND_US, UTC)
    hour = moment.hour + moment.minute / 60
    out["hour_sin"], out["hour_cos"] = (
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
    )
    day = moment.weekday()
    out["dow_sin"], out["dow_cos"] = (
        math.sin(2 * math.pi * day / 7),
        math.cos(2 * math.pi * day / 7),
    )

    v_bar = sum(v[i] for i in window(1440)) / 1440
    n_bar = sum(n[i] for i in window(1440)) / 1440
    for w in (1, 5, 15, 60):
        out[f"vol_rel_{w}"] = sum(v[i] for i in window(w)) / (w * v_bar)
    for w in (1, 15):
        out[f"count_rel_{w}"] = sum(n[i] for i in window(w)) / (w * n_bar)
    out["vwret_15"] = sum(v[i] * r(i) for i in window(15)) / sum(v[i] for i in window(15))

    big_v_bar = sum(big_v[i] for i in window(1440)) / 1440
    for w in (1, 5, 15, 60):
        total = sum(big_v[i] for i in window(w))
        out[f"buy_frac_{w}"] = sum(big_b[i] for i in window(w)) / total
        signed = sum(big_b[i] - (big_v[i] - big_b[i]) for i in window(w))
        out[f"signed_vol_{w}"] = signed / (w * big_v_bar)
    for w in (15, 60):
        before = range(t - w - 1439, t - w + 1)
        histogram = [sum(int(counts[i][k]) for i in before) for k in range(BINS)]
        kstar, accumulated = 0, 0
        for k, count in enumerate(histogram):
            accumulated += count
            if accumulated * 100 >= 95 * sum(histogram):
                kstar = k
                break
        large = sum(bin_volume[i][k] for i in window(w) for k in range(kstar + 1, BINS))
        out[f"large_frac_{w}"] = large / sum(big_v[i] for i in window(w))
    for w in (15, 60):
        vwap = sum(big_q[i] for i in window(w)) / sum(big_v[i] for i in window(w))
        out[f"vwap_dist_{w}"] = big_c[t] / vwap - 1

    for name in BOOK_FIELDS:
        out[f"book_{name}"] = bv[name][t]
    for k in (1, 20):
        last = bv[f"imbalance_{k}_last"]
        for w in (1, 5):
            out[f"imb{k}_chg_{w}"] = last[t] - last[t - w]
    return out


def family_valid(frame: FeatureFrame, t: int) -> dict[str, bool]:
    return {family: bool(frame.valid[family][t]) for family in FAMILIES}


ALL_VALID = dict.fromkeys(FAMILIES, True)


# ------------------------------------------------------------------------------ catálogo


def test_catalog_has_59_features_in_four_families() -> None:
    names = [name for name, _ in FEATURES]
    assert len(names) == len(set(names)) == 59
    per_family = {f: sum(fam == f for _, fam in FEATURES) for f in FAMILIES}
    assert per_family == {"candles": 18, "volume": 7, "flow": 12, "book": 22}
    assert len(catalog_id()) == 16
    assert catalog_id() == catalog_id()


# ------------------------------------------------------------------------------ fórmulas


@pytest.mark.parametrize("t", [T, T - 37, 1500])
def test_every_feature_matches_the_definition(t: int) -> None:
    series = make_series()
    frame = compute_features(series)
    assert family_valid(frame, t) == ALL_VALID
    expected = reference(series, t)
    assert expected.keys() == frame.values.keys()
    for name, value in expected.items():
        assert frame.values[name][t] == pytest.approx(value, rel=1e-9, abs=1e-15), name


def test_decision_time_is_two_seconds_after_the_minute() -> None:
    frame = compute_features(make_series())
    assert np.all(frame.decision_ts_us == frame.minute_ts_us + MINUTE_US + DECISION_DELAY_US)


def test_hour_and_weekday_use_the_end_of_the_minute() -> None:
    series = make_series(n=1500, seed=1)
    frame = compute_features(series)
    t = 1439  # segunda 23:59: o fim é terça 00:00
    assert frame.values["hour_sin"][t] == pytest.approx(0.0, abs=1e-12)
    assert frame.values["hour_cos"][t] == pytest.approx(1.0)
    assert frame.values["dow_sin"][t] == pytest.approx(math.sin(2 * math.pi / 7))


def test_first_valid_point_follows_the_longest_window() -> None:
    frame = compute_features(make_series())
    first = {family: int(np.argmax(frame.valid[family])) for family in FAMILIES}
    assert first == {"candles": 240, "volume": 1439, "flow": LOOKBACK_MINUTES - 1, "book": 5}


# ------------------------------------------------------------------------------ validade


@pytest.mark.parametrize(
    ("channel", "family", "inside", "outside"),
    [
        ("candles", "candles", T - 60, T - 61),
        ("candles", "volume", T - 59, T - 60),
        ("trades", "flow", T - 59, T - 60),
        ("book", "book", T - 5, T - 6),
        ("book", "book", T - 1, T - 2),
    ],
)
def test_short_windows_need_every_minute(
    channel: str, family: str, inside: int, outside: int
) -> None:
    series = make_series()
    assert not compute_features(changed(series, channel, invalid=[inside])).valid[family][T]
    assert compute_features(changed(series, channel, invalid=[outside])).valid[family][T]


def test_book_ignores_minutes_between_its_lags() -> None:
    series = changed(make_series(), "book", invalid=[T - 2, T - 3, T - 4])
    assert compute_features(series).valid["book"][T]


def test_candle_long_windows_need_95_percent_and_both_ends_of_ret_240() -> None:
    series = make_series()
    allowed = changed(series, "candles", invalid=range(T - 239, T - 227))  # 12 de 240
    frame = compute_features(allowed)
    assert frame.valid["candles"][T]
    c = series.candles.values["close"]
    kept = [c[i] for i in range(T - 239, T + 1) if not T - 239 <= i < T - 227]
    assert frame.values["sma_dist_240"][T] == pytest.approx(c[T] / (sum(kept) / 228) - 1)
    too_many = changed(series, "candles", invalid=range(T - 239, T - 226))  # 13 de 240
    assert not compute_features(too_many).valid["candles"][T]
    assert not compute_features(changed(series, "candles", invalid=[T - 240])).valid["candles"][T]


def test_day_windows_need_95_percent() -> None:
    series = make_series()
    for channel, family in (("candles", "volume"), ("trades", "flow")):
        ok = changed(series, channel, invalid=range(T - 1439, T - 1367))  # 72 de 1.440
        assert compute_features(ok).valid[family][T]
        bad = changed(series, channel, invalid=range(T - 1439, T - 1366))  # 73 de 1.440
        assert not compute_features(bad).valid[family][T]


def test_large_trade_threshold_window_needs_95_percent() -> None:
    """Os 1.440 minutos antes de J_60 vão de t - 1.499 a t - 60; 60 deles ficam fora de J_1440."""
    series = make_series()
    ok = changed(series, "trades", invalid=[*range(T - 1499, T - 1439), *range(T - 1439, T - 1427)])
    assert compute_features(ok).valid["flow"][T]  # 72 inválidos na janela do limite
    bad = changed(
        series, "trades", invalid=[*range(T - 1499, T - 1439), *range(T - 1439, T - 1426)]
    )
    assert not compute_features(bad).valid["flow"][T]  # 73


@pytest.mark.parametrize("w", [15, 60])
def test_large_trade_threshold_uses_exactly_the_1440_minutes_before_the_window(w: int) -> None:
    """Picos de negócios grandes logo dentro e logo fora da janela do limite mudam a faixa k*."""
    series = make_series()
    counts = np.zeros((N, BINS), dtype=np.int64)
    counts[:, 40] = 1
    volume = counts * 1.0
    spikes = {T - w - 1439: 90, T - w + 1: 95, T - w - 1440: 98}  # dentro, depois, antes
    for index, k in spikes.items():
        counts[index, k] = 1_000_000
    for k, size in ((60, 2.0), (92, 3.0), (96, 5.0), (99, 7.0)):
        volume[T - w + 1 : T + 1, k] = size
    total = volume.sum(axis=1)
    trades = Channel(
        {
            **series.trades.values,
            "size_bins_count": counts,
            "size_bins_volume": volume,
            "volume": total,
            "buy_volume": total / 2,
            "quote_volume": total * series.candles.values["close"],
        },
        series.trades.valid,
        series.trades.available_us,
    )
    series = with_channels(series, {"trades": trades})
    frame = compute_features(series)
    assert frame.valid["flow"][T]
    expected = reference(series, T)[f"large_frac_{w}"]
    assert frame.values[f"large_frac_{w}"][T] == pytest.approx(expected, rel=1e-12)
    in_window = volume[T - w + 1 : T + 1]
    assert expected == pytest.approx(in_window[:, 91:].sum() / in_window.sum())  # k* = 90


@pytest.mark.parametrize(
    ("channel", "families"), [("candles", ("candles", "volume")), ("trades", ("flow",))]
)
def test_minute_t_must_arrive_within_two_seconds(channel: str, families: tuple[str, ...]) -> None:
    """O minuto atrasado fica fora do seu próprio ponto, mas entra no seguinte (62 s)."""
    series = make_series()
    t = T - 1
    on_time = changed(series, channel, available={t: end_of(t) + 2 * SECOND_US})
    late = changed(series, channel, available={t: end_of(t) + 2 * SECOND_US + 1})
    for family in families:
        assert compute_features(on_time).valid[family][t]
        assert not compute_features(late).valid[family][t]
        assert compute_features(late).valid[family][t + 1]


def test_earlier_minutes_must_arrive_before_the_decision() -> None:
    series = make_series()
    ok = changed(series, "trades", available={T - 1: end_of(T - 1) + 62 * SECOND_US})
    assert compute_features(ok).valid["flow"][T]
    late = changed(series, "trades", available={T - 1: end_of(T - 1) + 62 * SECOND_US + 1})
    assert not compute_features(late).valid["flow"][T]


def test_undefined_feature_makes_the_point_invalid() -> None:
    series = make_series()
    quiet = changed(
        series,
        "trades",
        values={
            "volume": {T: 0.0},
            "buy_volume": {T: 0.0},
            "quote_volume": {T: 0.0},
            "close": {T: math.nan},
        },
    )
    frame = compute_features(quiet)
    assert family_valid(frame, T) == {**ALL_VALID, "flow": False}
    assert all(np.isnan(frame.values[name][T]) for name, fam in FEATURES if fam == "flow")

    no_measure = changed(series, "book", values={"spread_rel_mean": {T: math.nan}})
    assert family_valid(compute_features(no_measure), T) == {**ALL_VALID, "book": False}

    close = series.candles.values["close"]
    flat = changed(
        series, "candles", values={"close": dict.fromkeys(range(T - 14, T + 1), close[T])}
    )
    frame = compute_features(flat)
    assert not frame.valid["candles"][T]
    assert np.isnan(frame.values["rsi_14"][T])


# ------------------------------------------------------------------------------ point-in-time


def mixed_series(seed: int) -> MinuteSeries:
    """Série com minutos inválidos e atrasados espalhados, para exercitar a validade."""
    series = make_series(seed=seed)
    rng = np.random.default_rng(seed + 100)
    for channel in ("candles", "trades", "book"):
        invalid = rng.choice(N, size=N // 200, replace=False)
        late = rng.choice(N, size=N // 100, replace=False)
        delays = rng.integers(2 * SECOND_US, 120 * SECOND_US, late.size)
        available = {int(i): end_of(int(i)) + int(d) for i, d in zip(late, delays, strict=True)}
        series = changed(series, channel, invalid=list(invalid), available=available)
    return series


def perturb_after(series: MinuteSeries, t: int, seed: int) -> MinuteSeries:
    """Troca tudo o que só se sabe depois de t_d: os minutos após t e os recebimentos após t_d."""
    rng = np.random.default_rng(seed)
    decision = end_of(t) + DECISION_DELAY_US
    channels = {}
    for name in ("candles", "trades", "book"):
        old: Channel = getattr(series, name)
        values = {}
        for key, array in old.values.items():
            new = array.copy()
            noise = rng.uniform(0.5, 2.0, new[t + 1 :].shape)
            new[t + 1 :] = (new[t + 1 :] * noise).astype(new.dtype)
            values[key] = new
        valid = old.valid.copy()
        valid[t + 1 :] = rng.random(len(valid) - t - 1) < 0.5
        ready = old.available_us.copy()
        after = ready > decision
        after[t + 1 :] = True
        ready[after] = decision + rng.integers(1, 10 * 60 * SECOND_US, int(after.sum()))
        channels[name] = Channel(values, valid, ready)
    return with_channels(series, channels)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_nothing_after_the_decision_changes_any_feature(seed: int) -> None:
    series = mixed_series(seed)
    frame = compute_features(series)
    rng = np.random.default_rng(seed)
    points = sorted({1500, T - 1, *rng.integers(1500, T - 1, 6).tolist()})
    seen_valid = seen_invalid = 0
    for t in points:
        other = compute_features(perturb_after(series, t, seed=t))
        for family in FAMILIES:
            assert other.valid[family][t] == frame.valid[family][t], (t, family)
            seen_valid += int(frame.valid[family][t])
            seen_invalid += int(not frame.valid[family][t])
        for name, _ in FEATURES:
            a, b = frame.values[name][t], other.values[name][t]
            assert a == b or (np.isnan(a) and np.isnan(b)), (t, name)
    assert seen_valid and seen_invalid, "o teste precisa de pontos válidos e inválidos"


# ---------------------------------------------------------------- paridade e determinismo


@pytest.mark.parametrize("seed", [4, 5])
def test_last_1500_minutes_give_the_same_features(seed: int) -> None:
    series = mixed_series(seed)
    frame = compute_features(series)
    for t in range(LOOKBACK_MINUTES - 1, N, 13):
        last = compute_features(series.slice(t - LOOKBACK_MINUTES + 1, t + 1))
        for family in FAMILIES:
            assert last.valid[family][-1] == frame.valid[family][t], (t, family)
        for name, _ in FEATURES:
            np.testing.assert_allclose(
                last.values[name][-1], frame.values[name][t], rtol=1e-9, atol=0, err_msg=name
            )


def parquet_sha256(table: pa.Table) -> str:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def test_same_input_gives_the_same_output() -> None:
    first = compute_features(mixed_series(6)).to_table()
    second = compute_features(mixed_series(6)).to_table()
    assert first.equals(second)
    assert parquet_sha256(first) == parquet_sha256(second)


def test_output_table_has_stable_columns_and_nulls() -> None:
    series = make_series()
    frame = compute_features(series)
    table = frame.to_table()
    names = [name for name, _ in FEATURES]
    assert table.column_names == [
        "minute_ts_us",
        "decision_ts_us",
        *names,
        *(f"valid_{family}" for family in FAMILIES),
    ]
    assert all(table.schema.field(name).type == pa.float64() for name in names)
    assert table["ret_240"].null_count == 240
    assert table["valid_flow"].to_pylist()[LOOKBACK_MINUTES - 1 :] == [True] * (
        N - LOOKBACK_MINUTES + 1
    )


# ------------------------------------------------------------------------------ série de minutos


def minutes_table(rows: int = 3) -> pa.Table:
    columns: dict[str, list[object]] = {name: [None] * rows for name in MINUTES.names}
    columns["minute_ts_us"] = [T0 + i * MINUTE_US for i in range(rows)]
    columns["candle_close"] = [Decimal("60000.12345678"), None, Decimal("1.00000001")]
    columns["candle_volume"] = [Decimal("12345678901234.12345678"), None, Decimal("0")]
    columns["candle_valid"] = [True, None, False]
    columns["candle_available_us"] = [T0 + MINUTE_US + 5, None, T0 + 3 * MINUTE_US]
    columns["trades_size_bins_count"] = [list(range(BINS)), None, [0] * BINS]
    columns["trades_size_bins_volume"] = [[Decimal("0.5")] * BINS, None, [Decimal("0")] * BINS]
    columns["book_spread_rel_mean"] = [0.25, None, 0.5]
    return pa.table(columns, schema=MINUTES)


def test_series_from_table_converts_decimals_only_at_the_entry() -> None:
    series = series_from_table(minutes_table())
    close = series.candles.values["close"]
    assert close.dtype == np.float64
    assert close[0] == 60000.12345678 and np.isnan(close[1]) and close[2] == 1.00000001
    assert series.candles.values["volume"][0] == 12345678901234.12345678
    assert series.candles.valid.tolist() == [True, False, False]
    assert series.candles.available_us[1] == np.iinfo(np.int64).max
    counts = series.trades.values["size_bins_count"]
    assert counts.shape == (3, BINS) and counts[0, 7] == 7 and not counts[1].any()
    assert series.trades.values["size_bins_volume"][0, 0] == 0.5
    assert series.book.values["spread_rel_mean"].tolist()[0] == 0.25


def test_series_must_be_one_row_per_minute() -> None:
    table = minutes_table()
    shifted = table.set_column(
        0, "minute_ts_us", pa.array([T0, T0 + 2 * MINUTE_US, T0 + 3 * MINUTE_US], pa.int64())
    )
    with pytest.raises(ValueError, match="sem lacunas"):
        series_from_table(shifted)
