"""Dados do painel da coleta (doc 06 §8.4)."""

import csv
import hashlib
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from test_pipeline import END, START, build_scenario, run

from trader.ops.config import PipelineConfig
from trader.ops.state import StateStore
from trader.pipeline.dashboard_data import export, sync_from_s3
from trader.storage.layout import HOUR_US


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_export_from_pipeline_output(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    run(PipelineConfig(data_dir=data_dir), tmp_path / "replica", END + 2 * HOUR_US)
    # Um arquivo de backfill ilegível: se fosse lido, a exportação quebraria.
    archive = data_dir / "normalized/trades/symbol=BTCUSDT/date=2026-10-23/archive.parquet"
    archive.write_bytes(b"nao e parquet")

    report = export(data_dir, tmp_path / "out", "BTCUSDT")

    minutes = {r["minute"]: r for r in read_csv(tmp_path / "out/minutes.csv")}
    row = minutes["2026-10-23T14:00:00Z"]
    assert float(row["close"]) == 81524.01
    assert int(row["trades"]) == 10
    assert float(row["buy_volume"]) == 0.0008  # comprador agressor (m = false)
    assert int(row["snapshots"]) == 2
    book = read_csv(tmp_path / "out/book_snapshot.csv")
    assert [(r["side"], r["level"]) for r in book] == [
        ("compra", "1"),
        ("compra", "2"),
        ("venda", "1"),
        ("venda", "2"),
    ]
    quality = read_csv(tmp_path / "out/quality_hourly.csv")
    assert {r["channel"] for r in quality} == {
        "Negócios",
        "Candles",
        "Livro top 20",
        "Livro diferencial",
    }
    [gap] = read_csv(tmp_path / "out/gaps.csv")
    assert (gap["channel"], gap["cause"], gap["status"]) == ("Negócios", "trade_id_jump", "open")
    assert report.rows["delays_hourly.csv"] >= 1
    assert report.collection_start == "2026-10-23T13:00:00Z"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.downloads: list[str] = []

    def _etag(self, key: str) -> str:
        return '"' + hashlib.md5(self.objects[key]).hexdigest() + '"'

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        return {"ETag": self._etag(Key)}

    def get_paginator(self, name: str) -> Any:
        fake = self

        class Paginator:
            def paginate(self, Bucket: str, Prefix: str) -> Iterator[dict[str, Any]]:
                keys = sorted(k for k in fake.objects if k.startswith(Prefix))
                yield {"Contents": [{"Key": k, "ETag": fake._etag(k)} for k in keys]}

        return Paginator()

    def download_file(self, bucket: str, key: str, path: str) -> None:
        Path(path).write_bytes(self.objects[key])
        self.downloads.append(key)


def test_sync_downloads_only_live_files_that_changed(tmp_path: Path) -> None:
    state_path = tmp_path / "source.db"
    with StateStore(state_path) as state:
        state.session_opened("S1", "wss://x", ["a"], START, "v")  # 23/10/2026 14:00 UTC
    trades = "normalized/trades/symbol=BTCUSDT"
    objects = {
        "ops/state-backup.db": state_path.read_bytes(),
        f"{trades}/date=2026-10-23/14.parquet": b"a",
        f"{trades}/date=2026-10-24/00.parquet": b"b",
        f"{trades}/date=2026-10-22/23.parquet": b"antes da coleta",
        f"{trades}/date=2026-10-23/archive.parquet": b"backfill",
        "derived/quality_mask/symbol=BTCUSDT/date=2026-10-24/00.parquet": b"c",
    }
    s3 = FakeS3(objects)
    cache = tmp_path / "cache"

    first = sync_from_s3(s3, "bucket", cache, "BTCUSDT", today=date(2026, 10, 24))
    assert (first.downloaded, first.unchanged) == (4, 0)
    assert not (cache / f"{trades}/date=2026-10-22/23.parquet").exists()
    assert not (cache / f"{trades}/date=2026-10-23/archive.parquet").exists()

    second = sync_from_s3(s3, "bucket", cache, "BTCUSDT", today=date(2026, 10, 24))
    assert (second.downloaded, second.unchanged) == (0, 4)

    objects[f"{trades}/date=2026-10-24/00.parquet"] = b"b2"
    third = sync_from_s3(s3, "bucket", cache, "BTCUSDT", today=date(2026, 10, 24))
    assert third.downloaded == 1
    assert (cache / f"{trades}/date=2026-10-24/00.parquet").read_bytes() == b"b2"
