"""Alvo, pontos elegíveis e construção dos datasets (RF-223 a RF-229; doc 04 §4.3 e §4.4)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from test_minutes import (
    ARCHIVE_DAY,
    ARCHIVE_GAP,
    ARCHIVE_START,
    KLINE,
    LIVE_DAY,
    NO_EXECUTION,
    SYMBOL,
    build_data,
    price,
    row_of,
)

from trader.cli.main import main
from trader.core import holdout
from trader.core.holdout import HoldoutRefused
from trader.core.timeunits import MINUTE_US
from trader.datasets.labels import (
    FEE,
    SIMULATION_COLUMNS,
    SLIPPAGE,
    LabelParams,
    compute_labels,
    dataset_table,
    eligible_points,
    simulation_columns,
)
from trader.features.compute import FAMILIES, FEATURES, FeatureFrame, catalog_id
from trader.pipeline.datasets import (
    ACCESS_LOG,
    DATASETS,
    METADATA_KEY,
    Built,
    DatasetRequest,
    build_dataset,
    dataset_id,
)
from trader.pipeline.minutes import LocalSource
from trader.storage.objectstore import LocalDirectoryStore

NOW = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
PARAMS = LabelParams(horizons=(3, 5))


def reference(entry: float, exit_price: float) -> float:
    """O retorno líquido de doc 04 §4.3, em frações exatas."""
    f, s = Fraction(FEE), Fraction(SLIPPAGE)
    r = Fraction(exit_price) * (1 - s) * (1 - f) ** 2 / (Fraction(entry) * (1 + s)) - 1
    return float(r)


# ------------------------------------------------------------------ alvo (RF-223, RF-224)


def test_net_return_and_target_follow_the_formula() -> None:
    rng = np.random.default_rng(5)
    price_series = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.002, 40)))
    labels = compute_labels(price_series, np.ones(40, bool), PARAMS)
    for h in PARAMS.horizons:
        for t in range(40 - 1 - h):
            r = reference(price_series[t + 1], price_series[t + 1 + h])
            assert labels.valid[h][t]
            assert labels.entry_price[t] == price_series[t + 1]
            assert labels.exit_price[h][t] == price_series[t + 1 + h]
            assert labels.net_return[h][t] == pytest.approx(r, abs=1e-13)
    # sem mudança de preço, o retorno líquido é só o custo: cerca de -0,22%
    flat = compute_labels(np.full(10, 100.0), np.ones(10, bool), PARAMS)
    assert flat.net_return[3][0] == pytest.approx(reference(100, 100), abs=1e-15)
    assert -0.0023 < flat.net_return[3][0] < -0.0021


def test_target_validity_uses_trade_minutes_t_plus_1_to_t_plus_1_plus_h() -> None:
    n, h = 30, 3
    params = LabelParams(horizons=(h,))
    for bad, invalid_points in ((10, {6, 7, 8, 9}), (0, set()), (n - 1, {n - 2 - h})):
        valid = np.ones(n, bool)
        valid[bad] = False
        labels = compute_labels(np.full(n, 100.0), valid, params)
        expected = {t for t in range(n - 1 - h) if t not in invalid_points}
        assert set(np.flatnonzero(labels.valid[h])) == expected, bad


def test_missing_price_and_period_end() -> None:
    n, h = 20, 3
    params = LabelParams(horizons=(h,))
    price_series = np.full(n, 100.0)
    price_series[8] = np.nan
    price_series[12] = 0.0  # preço não positivo conta como ausente
    labels = compute_labels(price_series, np.ones(n, bool), params)
    invalid = {7, 8 - 1 - h, 11, 12 - 1 - h} | set(range(n - 1 - h, n))
    assert set(np.flatnonzero(~labels.valid[h])) == invalid
    assert np.isnan(labels.net_return[h][7]) and np.isnan(labels.net_return[h][n - 1])
    assert np.isnan(labels.entry_price[n - 1])
    short = compute_labels(np.full(3, 100.0), np.ones(3, bool), params)
    assert not short.valid[h].any(), "série menor que o horizonte"


def test_target_only_reads_the_entry_and_exit_minutes() -> None:
    n, h, t = 30, 5, 10
    params = LabelParams(horizons=(h,))
    base = np.linspace(100, 130, n)
    labels = compute_labels(base, np.ones(n, bool), params)
    for k in range(n):
        changed = base.copy()
        changed[k] *= 1.5
        other = compute_labels(changed, np.ones(n, bool), params)
        same = other.net_return[h][t] == labels.net_return[h][t]
        assert same == (k not in (t + 1, t + 1 + h)), k


def test_long_horizons_need_every_trade_minute_until_the_exit() -> None:
    """Revisão da trilha A (doc 04 §13.1): com H = 240, a validade olha 241 minutos."""
    n, bad = 600, 300
    valid = np.ones(n, bool)
    valid[bad] = False
    labels = compute_labels(np.full(n, 100.0), valid, LabelParams(horizons=(120, 240)))
    for h in (120, 240):
        last = n - 2 - h  # último t com a saída dentro do período
        hit = set(range(bad - 1 - h, bad))  # t + 1 <= bad <= t + 1 + H
        expected = {t for t in range(last + 1) if t not in hit}
        assert set(np.flatnonzero(labels.valid[h])) == expected, h
    assert labels.valid[240][bad - 242] and not labels.valid[240][bad - 241]


def test_horizons_must_be_positive_increasing_and_distinct() -> None:
    for horizons in ((), (0,), (-5, 15), (240, 120), (120, 120)):
        with pytest.raises(ValueError, match="horizontes inválidos"):
            LabelParams(horizons=horizons)
    assert LabelParams(horizons=(120, 240)).to_dict()["horizons"] == [120, 240]


def test_price_column_follows_the_latency() -> None:
    assert LabelParams().price_column == "exec_price_2500ms"
    assert LabelParams(latency_ms=2000).price_column == "exec_price_4000ms"
    with pytest.raises(ValueError, match="sem preço de execução"):
        _ = LabelParams(latency_ms=100).price_column
    assert LabelParams().stress_price_column == "exec_price_4000ms"
    assert LabelParams().to_dict() == {
        "horizons": [15, 60],
        "fee": 0.001,
        "slippage": 0.0001,
        "latency_ms": 500,
        "stress_latency_ms": 2000,
        "price_column": "exec_price_2500ms",
        "stress_price_column": "exec_price_4000ms",
    }


def test_simulation_columns() -> None:
    n = 130
    close = np.arange(n, dtype=float) + 1000
    high = close + 0.5
    valid = np.ones(n, bool)
    valid[100] = False
    stress = close + 0.25
    stress[50] = 0.0  # preço não positivo conta como ausente
    columns = simulation_columns(close, high, valid, stress)
    assert set(columns) == set(SIMULATION_COLUMNS)
    assert np.isnan(columns["close"][100]) and columns["close"][99] == 1099
    previous = columns["high_prev_60"]
    assert np.isnan(previous[:60]).all(), "menos de 60 minutos anteriores"
    assert previous[60] == high[:60].max() == 1059.5
    assert previous[99] == high[39:99].max(), "a máxima não inclui o minuto t"
    assert np.isnan(previous[101:161]).all(), "o minuto 100 inválido está na janela"
    entry = columns["entry_price_stress"]
    assert entry[10] == stress[11] and np.isnan(entry[49]) and np.isnan(entry[n - 1])


# ------------------------------------------------------------- pontos e tabela (RF-225, RF-226)


def frame_of(valid: dict[str, np.ndarray]) -> FeatureFrame:
    n = len(next(iter(valid.values())))
    ts = np.arange(n, dtype=np.int64) * MINUTE_US
    values = {name: np.where(valid[family], 1.0, np.nan) for name, family in FEATURES}
    return FeatureFrame(ts, ts + MINUTE_US + 2_000_000, values, valid)


def test_eligible_points_use_the_top_rung_of_the_track() -> None:
    n = 12
    valid = {family: np.ones(n, bool) for family in FAMILIES}
    valid["candles"][0] = valid["volume"][1] = valid["flow"][2] = valid["book"][3] = False
    labels = compute_labels(np.full(n, 100.0), np.ones(n, bool), LabelParams(horizons=(3,)))
    a = eligible_points(frame_of(valid), labels, "A")[3]
    b = eligible_points(frame_of(valid), labels, "B")[3]
    assert set(np.flatnonzero(a)) == set(range(3, 8)), "a trilha A não olha o livro (B6)"
    assert set(np.flatnonzero(b)) == set(range(4, 8)), "a trilha B exige o livro (B7)"


def test_dataset_table_columns() -> None:
    n = 12
    valid = {family: np.ones(n, bool) for family in FAMILIES}
    price_series = np.linspace(100, 90, n)
    labels = compute_labels(price_series, np.ones(n, bool), LabelParams(horizons=(3, 5)))
    simulation = simulation_columns(price_series, price_series, np.ones(n, bool), price_series)
    table = dataset_table(frame_of(valid), labels, "B", simulation)
    feature_names = [name for name, _ in FEATURES]
    assert table.column_names == [
        "minute_ts_us",
        "decision_ts_us",
        *feature_names,
        *(f"valid_{family}" for family in FAMILIES),
        "entry_price",
        *(
            f"{column}_h{h}"
            for h in (3, 5)
            for column in ("exit_price", "net_return", "target", "target_valid", "eligible")
        ),
        "close",
        "high_prev_60",
        "entry_price_stress",
    ]
    target = table["target_h3"].to_pylist()
    assert target[:8] == [False] * 8, "preço caindo: alvo negativo"
    assert target[8:] == [None] * 4, "sem alvo depois do fim do período"
    assert table["net_return_h3"].null_count == 4
    assert table["eligible_h5"].to_pylist() == [True] * 6 + [False] * 6
    assert table["entry_price"].to_pylist()[-1] is None


# ------------------------------------------------------------------ construção (RF-226 a RF-228)


def build(root: Path, out: Path, **kw: Any) -> Built:
    options: dict[str, Any] = {"track": "A", "start": LIVE_DAY, "end": ARCHIVE_DAY} | kw
    return build_dataset(
        LocalSource(root),
        symbol=SYMBOL,
        kline_channel=KLINE,
        request=DatasetRequest(**options),
        code="0.1.0+gteste",
        out_dir=out,
        store=LocalDirectoryStore(root),
        now=NOW,
        who="teste@mac",
    )


def test_build_writes_parquet_manifest_and_copy(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    built = build(root, tmp_path / "out")
    assert built.path == tmp_path / "out" / f"{built.dataset_id}.parquet"
    table = pq.read_table(built.path)
    assert table.num_rows == built.minutes == 2880
    summary = json.loads(table.schema.metadata[METADATA_KEY])
    assert summary["dataset_id"] == built.dataset_id
    assert summary["catalog_id"] == catalog_id()
    assert summary["inputs"] == 8
    assert "built_at" not in summary, "a hora da construção mudaria o arquivo (CA-07)"

    manifest = built.manifest
    assert dataset_id(manifest["identity"]) == built.dataset_id
    assert manifest["identity"]["label"]["price_column"] == "exec_price_2500ms"
    assert len(manifest["identity"]["inputs"]) == 8
    assert manifest["final_evaluation"] is False and manifest["holdout_access"] is None
    copy = root / DATASETS / built.dataset_id
    assert (copy / "data.parquet").read_bytes() == built.path.read_bytes()
    assert json.loads((copy / "manifest.json").read_text()) == manifest
    assert manifest["parquet_sha256"] == built.manifest["parquet_sha256"]
    assert not (root / ACCESS_LOG).exists()
    assert built.positives is not None


def test_target_comes_from_the_execution_price(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    table = pq.read_table(build(root, tmp_path / "out").path)
    t = ARCHIVE_START + 100 * MINUTE_US
    row = table.slice(row_of(t), 1).to_pylist()[0]
    entry = price(t + MINUTE_US) + Decimal("0.5")
    exit_price = price(t + 16 * MINUTE_US) + Decimal("0.5")
    assert row["entry_price"] == float(entry)
    assert row["exit_price_h15"] == float(exit_price)
    assert row["net_return_h15"] == pytest.approx(reference(float(entry), float(exit_price)))
    assert row["target_valid_h15"] and row["target_h15"] is False
    no_price = table.slice(row_of(NO_EXECUTION) - 1, 1).to_pylist()[0]
    assert no_price["entry_price"] is None and not no_price["target_valid_h15"]
    last = table.slice(table.num_rows - 16, 1).to_pylist()[0]
    assert last["target_valid_h15"] is False, "a saída cairia depois do fim do período"


def test_simulation_columns_come_from_candles_and_stress_price(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    table = pq.read_table(build(root, tmp_path / "out").path)
    t = ARCHIVE_START + 100 * MINUTE_US
    row = table.slice(row_of(t), 1).to_pylist()[0]
    assert row["close"] == pytest.approx(float(price(t)), abs=1e-9)  # DECIMAL → FLOAT64 (RF-212)
    expected_high = max(price(t - k * MINUTE_US) + 1 for k in range(1, 61))
    assert row["high_prev_60"] == pytest.approx(float(expected_high), abs=1e-9)
    assert row["entry_price_stress"] == float(price(t + MINUTE_US) + 1)
    gap = table.slice(row_of(ARCHIVE_GAP), 61).to_pylist()
    assert gap[0]["close"] is None, "minuto sem candle"
    assert all(r["high_prev_60"] is None for r in gap[1:]), "o minuto sem candle está na janela"
    after = table.slice(row_of(ARCHIVE_GAP + 61 * MINUTE_US), 1).to_pylist()[0]
    assert after["high_prev_60"] is not None
    no_price = table.slice(row_of(NO_EXECUTION) - 1, 1).to_pylist()[0]
    assert no_price["entry_price_stress"] is None


def test_rebuilding_gives_the_same_file_and_id(tmp_path: Path) -> None:
    """CA-07: mesmas entradas e mesma versão do código, mesmo conteúdo."""
    root = build_data(tmp_path / "data")
    first = build(root, tmp_path / "a")
    second = build(root, tmp_path / "b")
    assert first.dataset_id == second.dataset_id
    assert first.path.read_bytes() == second.path.read_bytes()
    other = build(root, tmp_path / "c", track="B")
    assert other.dataset_id != first.dataset_id
    shorter = build(root, tmp_path / "d", end=LIVE_DAY)
    assert shorter.dataset_id != first.dataset_id


def test_changed_input_changes_the_id(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    first = build(root, tmp_path / "a")
    path = root / "derived/minute_trades" / f"symbol={SYMBOL}" / "date=2026-10-24/archive.parquet"
    pq.write_table(pq.read_table(path), path, compression="snappy", row_group_size=100)
    assert build(root, tmp_path / "b").dataset_id != first.dataset_id


def test_track_a_holdout_is_refused_before_reading(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    with pytest.raises(HoldoutRefused, match="RF-228"):
        build(root, tmp_path / "out", start=date(2026, 10, 22))
    assert not (tmp_path / "out").exists()
    assert not (root / ACCESS_LOG).exists()


def test_final_evaluation_logs_access_before_reading(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    log = root / ACCESS_LOG
    seen: list[bool] = []

    class Spy(LocalSource):
        def keys(self, prefix: str) -> list[str]:
            seen.append(any(log.glob("*.json")))
            return super().keys(prefix)

    built = build_dataset(
        Spy(root),
        symbol=SYMBOL,
        kline_channel=KLINE,
        request=DatasetRequest("A", date(2026, 10, 22), ARCHIVE_DAY, final_evaluation=True),
        code="0.1.0+gteste",
        out_dir=tmp_path / "out",
        store=LocalDirectoryStore(root),
        now=NOW,
        who="teste@mac",
    )
    assert seen and all(seen), "o registro vem antes de qualquer leitura"
    request = json.loads((log / "20261107T210000.000000Z.json").read_text())
    assert request["stage"] == "request" and request["holdout_a_days"] == 1
    assert (request["who"], request["track"], request["start"]) == ("teste@mac", "A", "2026-10-22")
    result = json.loads((log / "20261107T210000.000000Z.result.json").read_text())
    assert result["dataset_id"] == built.dataset_id
    assert result["parquet_sha256"] == built.manifest["parquet_sha256"]
    assert built.manifest["final_evaluation"] is True
    assert built.manifest["holdout_access"] == f"{ACCESS_LOG}/20261107T210000.000000Z.json"
    assert built.positives is None, "sem estatísticas do alvo na avaliação final"


def test_track_b_freeze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = build_data(tmp_path / "data")
    assert holdout.HOLDOUT_B_START is None, "o congelamento da trilha B ainda não aconteceu"
    monkeypatch.setattr(holdout, "HOLDOUT_B_START", datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    with pytest.raises(HoldoutRefused, match="trilha B"):
        build(root, tmp_path / "out", track="B")
    assert build(root, tmp_path / "out", track="B", end=LIVE_DAY).minutes == 1440
    monkeypatch.setattr(holdout, "HOLDOUT_B_START", datetime(2026, 10, 25, 0, 0, tzinfo=UTC))
    assert build(root, tmp_path / "out", track="B").minutes == 2880


def test_unknown_track(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trilha desconhecida"):
        build(tmp_path, tmp_path / "out", track="C")


# ------------------------------------------------------------------ comando (RF-229)


def test_dataset_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = build_data(tmp_path / "data")
    out = tmp_path / "datasets"
    args = ["dataset", "--data-dir", str(root), "--track", "A", "--out-dir", str(out)]
    assert main([*args, "--start", "2026-10-23", "--end", "2026-10-24"]) == 0
    printed = capsys.readouterr().out
    (path,) = out.glob("*.parquet")
    assert f"dataset {path.stem} (trilha A) · minutos: 2880" in printed
    assert "H = 15 min" in printed and "H = 60 min" in printed
    manifest = json.loads((out / f"{path.stem}.manifest.json").read_text())
    assert manifest["identity"]["code_version"].startswith("0.1.0")
    assert (root / DATASETS / path.stem / "manifest.json").exists()

    assert main([*args, "--start", "2026-10-22", "--end", "2026-10-23"]) == 2
    assert "recusado" in capsys.readouterr().err
    assert len(list(out.glob("*.parquet"))) == 1


def test_dataset_command_with_other_horizons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--horizons` vai para os parâmetros do alvo e para o id (doc 03 §5.5; RF-229)."""
    root = build_data(tmp_path / "data")
    out = tmp_path / "datasets"
    args = ["dataset", "--data-dir", str(root), "--track", "A", "--out-dir", str(out)]
    period = ["--start", "2026-10-23", "--end", "2026-10-24"]
    assert main([*args, *period, "--horizons", "120,240"]) == 0
    printed = capsys.readouterr().out
    assert "H = 120 min" in printed and "H = 240 min" in printed and "H = 15 min" not in printed
    (path,) = out.glob("*.parquet")
    names = pq.read_schema(path).names
    assert {"eligible_h120", "target_h240", "net_return_h240"} <= set(names)
    assert not any(n.endswith(("_h15", "_h60")) for n in names)
    manifest = json.loads((out / f"{path.stem}.manifest.json").read_text())
    assert manifest["identity"]["label"]["horizons"] == [120, 240]

    assert main([*args, *period]) == 0, "os horizontes padrão dão outro dataset"
    capsys.readouterr()
    assert len(list(out.glob("*.parquet"))) == 2

    assert main([*args, *period, "--horizons", "240,120"]) == 2
    assert "horizontes inválidos" in capsys.readouterr().err
