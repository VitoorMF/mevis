"""Job de agregados por minuto sob demanda (doc 03 §7.8; RF-176, RF-179, RF-187)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trader.cli.main import main
from trader.core.timeunits import MINUTE_US
from trader.ops.config import PipelineConfig
from trader.pipeline.aggregates import (
    AggregatesJob,
    HoldoutRefused,
    InPlaceStore,
    JobOptions,
    day_start_us,
)
from trader.storage import parquet
from trader.storage.layout import HOUR_US, archive_dataset_path, dataset_path
from trader.storage.objectstore import LocalDirectoryStore
from trader.storage.rawfiles import file_sha256

D1, D2, D3, HOLDOUT_DAY = date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23)
LIVE_DAY = date(2026, 10, 24)
LIVE_HOUR = day_start_us(LIVE_DAY) // HOUR_US + 10
TRADE, TOP20 = "btcusdt@trade", "btcusdt@depth20@100ms"


def at(day: date, hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=UTC).timestamp() * 1e6)


def trade_row(trade_id: int, ts: int, *, recv: int | None = None) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "price": Decimal("65000.10"),
        "qty": Decimal("0.01"),
        "quote_qty": Decimal("650.001"),
        "trade_ts_us": ts,
        "event_ts_us": None if recv is None else ts,
        "recv_ts_us": recv,
        "is_buyer_maker": trade_id % 2 == 0,
        "aggressor": "S" if trade_id % 2 == 0 else "B",
        "symbol": "BTCUSDT",
        "origin": "archive" if recv is None else "live",
        "provenance": "teste",
        "schema_version": 1,
    }


def write_archive_day(root: Path, day: date, trades: list[tuple[int, int]]) -> None:
    path = archive_dataset_path(root, "trades", "BTCUSDT", day)
    parquet.write_rows(path, parquet.TRADES, [trade_row(i, ts) for i, ts in trades])


def write_live_hour(root: Path) -> None:
    start = LIVE_HOUR * HOUR_US
    trades = [
        trade_row(500 + i, start + i * MINUTE_US + 1, recv=start + i * MINUTE_US + 9)
        for i in range(3)
    ]
    parquet.write_rows(
        dataset_path(root, "normalized", "trades", "BTCUSDT", LIVE_HOUR), parquet.TRADES, trades
    )
    book = {
        "last_update_id": 1,
        "recv_ts_us": start + 5,
        "bid_px": [Decimal("100")],
        "bid_qty": [Decimal("1")],
        "ask_px": [Decimal("101")],
        "ask_qty": [Decimal("3")],
        "symbol": "BTCUSDT",
        "origin": "live",
        "provenance": "S1",
        "schema_version": 1,
    }
    parquet.write_rows(
        dataset_path(root, "normalized", "book_top20", "BTCUSDT", LIVE_HOUR),
        parquet.BOOK_TOP20,
        [book],
    )
    mask = [
        {
            "symbol": "BTCUSDT",
            "channel": channel,
            "minute_ts_us": start + m * MINUTE_US,
            "status": "missing" if (channel, m) == (TRADE, 1) else "ok",
            "details": "",
            "schema_version": 1,
        }
        for channel in (TRADE, TOP20)
        for m in range(60)
    ]
    parquet.write_rows(
        dataset_path(root, "derived", "quality_mask", "BTCUSDT", LIVE_HOUR),
        parquet.QUALITY_MASK,
        mask,
    )


@pytest.fixture
def replica(tmp_path: Path) -> Path:
    root = tmp_path / "replica"
    write_archive_day(
        root, D1, [(1, at(D1, 0, 0, 10)), (2, at(D1, 12, 0)), (3, at(D1, 23, 59, 30))]
    )
    write_archive_day(root, D2, [(4, at(D2, 0, 0, 20)), (5, at(D2, 23, 58))])
    write_archive_day(root, D3, [(9, at(D3, 0, 1)), (10, at(D3, 10, 0))])  # salto 5 → 9
    write_archive_day(root, HOLDOUT_DAY, [(11, at(HOLDOUT_DAY, 0, 0, 5))])
    write_live_hour(root)
    return root


def run_job(
    tmp_path: Path, replica: Path, start: date, end: date, job_id: str = "j1", **options: Any
) -> dict[str, Any]:
    config = PipelineConfig(data_dir=tmp_path / "work", replica_dir=replica)
    job = AggregatesJob(
        config,
        LocalDirectoryStore(replica),
        JobOptions(start=start, end=end, job_id=job_id, code_version="teste", **options),
    )
    return job.run().manifest


def statuses(root: Path, day: date) -> list[str]:
    path = archive_dataset_path(root, "minute_trades", "BTCUSDT", day, layer="derived")
    return list(pq.read_table(path)["status"].to_pylist())


def test_archive_days_with_jump_between_days(tmp_path: Path, replica: Path) -> None:
    manifest = run_job(tmp_path, replica, D1, D3)

    assert manifest["status"] == "ok"
    assert [u["day"] for u in manifest["units"]] == ["2026-07-20", "2026-07-21", "2026-07-22"]
    assert manifest["trades"] == 7
    assert statuses(replica, D1) == ["ok"] * 1440
    assert statuses(replica, D2)[-3:] == ["ok", "missing", "missing"]  # desde 23:58
    assert statuses(replica, D2).count("missing") == 2
    assert statuses(replica, D3)[:3] == ["missing", "missing", "ok"]  # até 00:01
    assert statuses(replica, D3).count("missing") == 2
    assert "salto de id entre 2026-07-21 e 2026-07-22" in manifest["warnings"]
    assert "2026-07-23: vizinho no holdout, fronteira não conferida" in manifest["warnings"]

    keys = {i["key"] for i in manifest["inputs"]}
    assert not any("2026-07-23" in k for k in keys)  # o holdout não foi lido
    for item in manifest["inputs"]:
        assert item["sha256"] == file_sha256(replica / item["key"])
    assert len(manifest["outputs"]) == 3
    for item in manifest["outputs"]:
        assert item["sha256"] == file_sha256(replica / item["key"])
        assert item["rows"] == 1440

    stored = json.loads((replica / "derived/ops/jobs/j1/manifest.json").read_text())
    assert stored == manifest
    work = tmp_path / "work"
    assert not list((work / "normalized").rglob("*.parquet"))  # entradas baixadas foram apagadas
    assert not list((work / "derived").rglob("*.parquet"))  # saídas enviadas também


def test_holdout_is_refused_without_the_final_evaluation_option(
    tmp_path: Path, replica: Path
) -> None:
    with pytest.raises(HoldoutRefused, match="holdout da trilha A"):
        run_job(tmp_path, replica, D3, HOLDOUT_DAY)
    assert not (replica / "derived/ops/jobs").exists()
    assert not (replica / "derived/minute_trades").exists()


def test_holdout_with_the_final_evaluation_option(tmp_path: Path, replica: Path) -> None:
    manifest = run_job(tmp_path, replica, HOLDOUT_DAY, HOLDOUT_DAY, allow_holdout=True)
    assert manifest["holdout_days"] == 1
    assert manifest["args"]["allow_holdout"] is True
    edge = [i for i in manifest["inputs"] if i.get("edge_only")]
    assert [i["key"].split("/")[3] for i in edge] == ["date=2026-07-22"]  # vizinho conferido
    assert not any("salto" in w for w in manifest["warnings"])  # 10 → 11: contínuo
    assert statuses(replica, HOLDOUT_DAY) == ["ok"] * 1440


def test_live_hours_use_the_mask(tmp_path: Path, replica: Path) -> None:
    manifest = run_job(tmp_path, replica, LIVE_DAY, LIVE_DAY)
    assert [(u["kind"], u["hour"]) for u in manifest["units"]] == [("live", 10)]
    minutes = pq.read_table(
        dataset_path(replica, "derived", "minute_trades", "BTCUSDT", LIVE_HOUR)
    ).to_pylist()
    assert [m["trade_count"] for m in minutes[:4]] == [1, 1, 1, 0]
    assert [m["status"] for m in minutes[:3]] == ["ok", "missing", "ok"]
    assert minutes[0]["last_recv_ts_us"] == LIVE_HOUR * HOUR_US + 9
    book = pq.read_table(
        dataset_path(replica, "derived", "minute_book", "BTCUSDT", LIVE_HOUR)
    ).to_pylist()
    assert book[0]["imbalance_1_last"] == 0.25
    assert "2026-10-24" not in " ".join(manifest["warnings"])


def test_repeated_and_parallel_runs_give_the_same_outputs(tmp_path: Path, replica: Path) -> None:
    first = run_job(tmp_path, replica, D1, D3, job_id="a")
    again = run_job(tmp_path, replica, D1, D3, job_id="b", workers=2)
    assert again["outputs"] == first["outputs"]
    assert again["args"]["workers"] == 2


def test_without_replica_works_in_place(tmp_path: Path, replica: Path) -> None:
    config = PipelineConfig(data_dir=replica)
    options = JobOptions(start=D1, end=D2, job_id="local", code_version="teste")
    manifest = AggregatesJob(config, InPlaceStore(replica), options).run().manifest
    assert len(manifest["outputs"]) == 2
    assert archive_dataset_path(replica, "trades", "BTCUSDT", D1).exists()  # entrada fica
    assert (replica / manifest["outputs"][0]["key"]).exists()


def test_cli(tmp_path: Path, replica: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["aggregates", "--data-dir", str(tmp_path / "w"), "--replica-dir", str(replica)]
    assert main([*base, "--start", "2026-07-20", "--end", "2026-07-20", "--job-id", "c1"]) == 0
    out = capsys.readouterr().out
    assert "dias de arquivo: 1" in out
    assert "manifesto: derived/ops/jobs/c1/manifest.json" in out
    assert main([*base, "--start", "2026-07-22", "--end", "2026-07-23"]) == 2
    assert "holdout" in capsys.readouterr().err
    assert main([*base, "--start", "2026-07-20", "--end", "2026-07-20", "--job-id", "a b"]) == 2


def test_failed_run_still_writes_manifest(tmp_path: Path, replica: Path) -> None:
    path = archive_dataset_path(replica, "trades", "BTCUSDT", D1)
    pq.write_table(pa.table({"trade_id": [1]}), path)  # sem as colunas esperadas
    with pytest.raises(Exception):  # noqa: B017 — qualquer erro serve
        run_job(tmp_path, replica, D1, D1, job_id="bad")
    stored = json.loads((replica / "derived/ops/jobs/bad/manifest.json").read_text())
    assert stored["status"] == "failed"
    assert stored["error"]
