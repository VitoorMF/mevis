"""Recuperação de lacunas como etapa do pipeline (doc 06 §9.1; SRS UC-05, RF-146 a RF-149)."""

import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
from test_pipeline import TRADE, RawBuilder, closed_kline, trade

from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.ops.config import PipelineConfig
from trader.ops.state import GapRecord, StateStore
from trader.pipeline.hourly import HourlyPipeline, PipelineReport
from trader.recovery.gaps import GapRecovery, _write_raw
from trader.sources.binance_archive import ArchiveClient
from trader.storage.layout import HOUR_US, dataset_path

START = 1_792_764_000_000_000  # 2026-10-23 14:00 UTC
HOUR = START // HOUR_US
KLINE = "btcusdt@kline_1m"
ARCHIVE_PATH = "/data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2026-10-23.zip"
NOW = START + 2 * 86_400_000_000  # o arquivo diário de 23/10 já saiu


def archive_line(trade_id: int) -> str:
    ts = START + trade_id * SECOND_US
    return f"{trade_id},81524.01000000,0.00008000,6.52192080,{ts},False,True"


def kline_row(open_ts: int) -> list[Any]:
    return [
        open_ts,
        "81520.00000000",
        "81530.00000000",
        "81510.00000000",
        "81524.01000000",
        "1.00000000",
        open_ts + MINUTE_US - 1,
        "81520.00000000",
        10,
        "0.50000000",
        "40760.00000000",
        "0",
    ]


class FakeBinance:
    """REST de klines e arquivos diários simulados."""

    def __init__(self, archive_ids: list[int] | None, klines: list[int], kline_status: int = 200):
        self.archive = None
        if archive_ids is not None:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                lines = "\n".join(archive_line(i) for i in archive_ids)
                bundle.writestr("BTCUSDT-trades-2026-10-23.csv", lines + "\n")
            self.archive = buffer.getvalue()
        self.klines = klines
        self.kline_status = kline_status
        self.calls: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        if path == "/api/v3/klines":
            assert request.headers["X-MBX-TIME-UNIT"] == "MICROSECOND"
            if self.kline_status != 200:
                return httpx.Response(self.kline_status, headers={"Retry-After": "30"})
            return httpx.Response(200, json=[kline_row(t) for t in self.klines])
        if self.archive is None:
            return httpx.Response(404)
        if path == ARCHIVE_PATH + ".CHECKSUM":
            digest = hashlib.sha256(self.archive).hexdigest()
            return httpx.Response(200, content=f"{digest}  x.zip\n".encode())
        if path == ARCHIVE_PATH:
            return httpx.Response(200, content=self.archive)
        return httpx.Response(404)


def build(data_dir: Path) -> None:
    """Negócios 1-3 e 7-9 ao vivo (faltam 4-6); candles dos minutos 0 e 2 (falta o 1)."""
    raw = RawBuilder(data_dir)
    for i in (1, 2, 3, 7, 8, 9):
        raw.add("S1", TRADE, START + i * SECOND_US + 1000, trade(i, START + i * SECOND_US))
    raw.add("S1", KLINE, START + MINUTE_US + 500_000, closed_kline(START))
    raw.add("S1", KLINE, START + 3 * MINUTE_US + 500_000, closed_kline(START + 2 * MINUTE_US))
    raw.writer.close()
    with StateStore(data_dir / "state.db") as state:
        state.session_opened("S1", "wss://x", [TRADE], START - HOUR_US, "test")
        state.session_closed("S1", START + 2 * HOUR_US, "shutdown", 0)
        for minute in range(-60, 121, 5):
            local = START + minute * MINUTE_US
            state.record_clock_offset(local, local + 1_000, 140_000, 1_000)
        state.record_gap(
            GapRecord(
                TRADE, START + 3 * SECOND_US, START + 7 * SECOND_US, "trade_id_jump", 1, True, 4, 6
            )
        )
        state.record_gap(
            GapRecord(KLINE, START + MINUTE_US, START + 2 * MINUTE_US, "kline_missing", 2, True)
        )


def run(data_dir: Path, fake: FakeBinance) -> PipelineReport:
    config = PipelineConfig(
        data_dir=data_dir,
        rest_base_url="https://rest.test",
        archive_base_url="https://archive.test",
    ).validate()
    with (
        StateStore(data_dir / "state.db") as state,
        httpx.Client(transport=httpx.MockTransport(fake.handler)) as client,
    ):
        archive = ArchiveClient(client, config.archive_base_url, sleep=lambda _: None)
        recovery = GapRecovery(config, state, client, archive)
        return HourlyPipeline(config, state, None, recovery).run(NOW)


def table(data_dir: Path, layer: str, dataset: str) -> list[dict[str, Any]]:
    path = dataset_path(data_dir, layer, dataset, "BTCUSDT", HOUR)
    return list(pq.read_table(path).to_pylist())


def gap_statuses(data_dir: Path) -> list[tuple[str, str]]:
    with StateStore(data_dir / "state.db") as state:
        return [(g.record.channel, g.status) for g in state.gaps_since(0)]


def test_recovers_trades_from_archive_and_klines_from_rest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build(data_dir)
    report = run(data_dir, FakeBinance(archive_ids=list(range(1, 10)), klines=[START + MINUTE_US]))

    assert (report.recovered_gaps, report.irrecoverable_gaps, report.pending_gaps) == (2, 0, 0)
    assert gap_statuses(data_dir) == [(TRADE, "recovered"), (KLINE, "recovered")]

    trades = {t["trade_id"]: t for t in table(data_dir, "normalized", "trades")}
    assert sorted(trades) == list(range(1, 10))
    assert {trades[i]["origin"] for i in (4, 5, 6)} == {"archive"}
    assert trades[5]["recv_ts_us"] is None
    assert trades[5]["provenance"].startswith("BTCUSDT-trades-2026-10-23.zip@")
    assert trades[3]["origin"] == "live"

    klines = {k["open_ts_us"]: k for k in table(data_dir, "normalized", "klines_1m")}
    assert sorted(klines) == [START, START + MINUTE_US, START + 2 * MINUTE_US]
    assert klines[START + MINUTE_US]["origin"] == "rest"

    mask = {
        (r["channel"], (r["minute_ts_us"] - START) // MINUTE_US): r["status"]
        for r in table(data_dir, "derived", "quality_mask")
    }
    assert mask[(TRADE, 0)] == "recovered"
    assert mask[(KLINE, 1)] == "recovered"
    assert mask[(KLINE, 0)] == "ok"

    again = run(data_dir, FakeBinance(archive_ids=None, klines=[]))
    assert (again.recovered_gaps, again.pending_gaps) == (0, 0), "lacunas fechadas não voltam"


def test_live_copy_wins_over_recovered_duplicate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build(data_dir)
    payload = {"file": "f.zip", "sha256": "x", "unit": "us", "row": archive_line(2).split(",")}
    line = json.dumps(payload, separators=(",", ":")).encode()
    config = PipelineConfig(data_dir=data_dir)
    _write_raw(config, "archive:trades", HOUR, "R1", [(NOW, line)])
    report = run(data_dir, FakeBinance(archive_ids=list(range(1, 10)), klines=[START + MINUTE_US]))

    trades = {t["trade_id"]: t for t in table(data_dir, "normalized", "trades")}
    assert trades[2]["origin"] == "live"
    assert report.duplicates[TRADE] == 1


def test_unpublished_archive_and_rate_limit_leave_gaps_open(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build(data_dir)
    fake = FakeBinance(archive_ids=None, klines=[], kline_status=429)
    report = run(data_dir, fake)

    assert (report.recovered_gaps, report.pending_gaps) == (0, 2)
    assert gap_statuses(data_dir) == [(TRADE, "open"), (KLINE, "open")]
    assert fake.calls["/api/v3/klines"] == 1
    assert sorted(t["trade_id"] for t in table(data_dir, "normalized", "trades")) == [
        1,
        2,
        3,
        7,
        8,
        9,
    ]


def test_ids_missing_from_archive_make_the_gap_irrecoverable(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build(data_dir)
    report = run(data_dir, FakeBinance(archive_ids=[1, 2, 3, 4, 6, 7, 8, 9], klines=[]))

    assert (report.recovered_gaps, report.irrecoverable_gaps) == (0, 2)
    assert gap_statuses(data_dir) == [(TRADE, "irrecoverable"), (KLINE, "irrecoverable")]
    ids = sorted(t["trade_id"] for t in table(data_dir, "normalized", "trades"))
    assert ids == [1, 2, 3, 4, 6, 7, 8, 9]
