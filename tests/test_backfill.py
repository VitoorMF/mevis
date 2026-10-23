"""Backfill dos arquivos públicos (doc 06 §7; SRS UC-04, RF-141 a RF-144)."""

import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import zstandard

from trader.cli.main import main
from trader.ops.config import PipelineConfig
from trader.ops.state import StateStore
from trader.pipeline.backfill import Backfill, BackfillReport
from trader.sources.binance_archive import ArchiveClient, ArchiveError, ArchiveFile, plan_files
from trader.storage.layout import StorageError
from trader.storage.objectstore import LocalDirectoryStore

DAY = date(2026, 10, 22)
DAY_START_US = 1_792_627_200_000_000  # 22/10/2026 00:00 UTC
TRADES_PATH = "/data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2026-10-22.zip"


def trade_line(trade_id: int, price: str = "84093.42000000", qty: str = "0.00491000") -> str:
    quote = f"{Decimal(price) * Decimal(qty):.8f}"
    return f"{trade_id},{price},{qty},{quote},{DAY_START_US + trade_id},False,True"


def kline_line(open_ms: int) -> str:
    return f"{open_ms},100.0,101.0,99.0,100.5,2.0,{open_ms + 59_999},201.0,7,1.0,100.5,0"


def zipped(name: str, lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name.replace(".zip", ".csv"), "\n".join(lines) + "\n")
    return buffer.getvalue()


class FakeArchive:
    """`data.binance.vision` simulado: ZIP + `.CHECKSUM` por caminho, com falhas programáveis."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.wrong_checksum: set[str] = set()
        self.failures: dict[str, list[int]] = {}
        self.calls: Counter[str] = Counter()

    def add(self, path: str, lines: list[str]) -> None:
        self.files[path] = zipped(path.rsplit("/", 1)[1], lines)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        if self.failures.get(path):
            status = self.failures[path].pop(0)
            return httpx.Response(status, headers={"Retry-After": "7"} if status == 429 else {})
        if path.endswith(".CHECKSUM"):
            body = self.files.get(path.removesuffix(".CHECKSUM"))
            if body is None:
                return httpx.Response(404)
            digest = hashlib.sha256(body).hexdigest()
            if path.removesuffix(".CHECKSUM") in self.wrong_checksum:
                digest = "0" * 64
            name = path.rsplit("/", 1)[1].removesuffix(".CHECKSUM")
            return httpx.Response(200, content=f"{digest}  {name}\n".encode())
        body = self.files.get(path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)


def run(
    tmp_path: Path,
    fake: FakeArchive,
    dataset: str,
    start: date,
    end: date,
    store: LocalDirectoryStore | None = None,
) -> tuple[BackfillReport, list[float]]:
    sleeps: list[float] = []
    config = PipelineConfig(data_dir=tmp_path / "data").validate()
    with (
        StateStore(config.data_dir / "state.db") as state,
        httpx.Client(transport=httpx.MockTransport(fake.handler)) as client,
    ):
        archive = ArchiveClient(client, "https://archive.test", sleep=sleeps.append)
        report = Backfill(config, state, store, archive).run(dataset, start, end)
    return report, sleeps


def read_quarantine(path: Path) -> list[dict[str, str]]:
    data = zstandard.ZstdDecompressor().stream_reader(path.open("rb")).read()
    return [json.loads(line) for line in data.splitlines()]


class TestPlan:
    def test_trades_are_daily(self) -> None:
        files = plan_files("trades", "BTCUSDT", date(2026, 2, 27), date(2026, 3, 2))
        assert [f.name for f in files] == [
            "BTCUSDT-trades-2026-02-27.zip",
            "BTCUSDT-trades-2026-02-28.zip",
            "BTCUSDT-trades-2026-03-01.zip",
            "BTCUSDT-trades-2026-03-02.zip",
        ]
        assert files[0].url_path == TRADES_PATH.replace("2026-10-22", "2026-02-27")

    def test_klines_use_monthly_files_for_complete_months(self) -> None:
        files = plan_files("klines_1m", "BTCUSDT", date(2022, 12, 30), date(2023, 3, 2))
        assert [f.name for f in files] == [
            "BTCUSDT-1m-2022-12-30.zip",
            "BTCUSDT-1m-2022-12-31.zip",
            "BTCUSDT-1m-2023-01.zip",
            "BTCUSDT-1m-2023-02.zip",
            "BTCUSDT-1m-2023-03-01.zip",
            "BTCUSDT-1m-2023-03-02.zip",
        ]
        january = files[2]
        assert january.url_path == "/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2023-01.zip"
        assert (january.first_day, january.last_day) == (date(2023, 1, 1), date(2023, 1, 31))


def test_imports_a_day_of_trades_with_quarantine_and_report(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(
        TRADES_PATH,
        [
            trade_line(10),
            trade_line(10),  # repetição idêntica: descartada
            trade_line(11, price="0.00000000"),  # preço zero: quarentena
            trade_line(14),  # 12 e 13 ausentes
            f"15,1.0,1.0,1.00000000,{DAY_START_US + 86_400_000_000},False,True",  # outro dia
        ],
    )
    report, _ = run(tmp_path, fake, "trades", DAY, DAY)

    [row] = report.files
    assert row.status == "imported"
    assert (row.rows, row.duplicates, row.quarantined, row.missing_ids) == (2, 1, 2, 3)
    data = tmp_path / "data"
    table = pq.read_table(data / "normalized/trades/symbol=BTCUSDT/date=2026-10-22/archive.parquet")
    assert table.column("trade_id").to_pylist() == [10, 14]
    assert set(table.column("origin").to_pylist()) == {"archive"}
    assert table.column("recv_ts_us").null_count == 2
    sha = hashlib.sha256(fake.files[TRADES_PATH]).hexdigest()
    assert set(table.column("provenance").to_pylist()) == {f"BTCUSDT-trades-2026-10-22.zip@{sha}"}
    quarantine = data / "quarantine/binance-spot/btcusdt/date=2026-10-22/archive-trades.jsonl.zst"
    reasons = sorted(entry["reason"] for entry in read_quarantine(quarantine))
    assert reasons == ["fora do período do arquivo", "non_positive_price"]
    assert not list((data / "tmp").rglob("*.zip")), "o ZIP baixado é apagado"


def test_monthly_klines_in_milliseconds_are_split_by_day(tmp_path: Path) -> None:
    feb_1_ms = 1_706_745_600_000  # 01/02/2024 00:00 UTC, arquivo ainda em ms
    path = "/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-02.zip"
    fake = FakeArchive()
    fake.add(
        path,
        [kline_line(feb_1_ms), kline_line(feb_1_ms + 60_000), kline_line(feb_1_ms + 86_400_000)],
    )
    report, _ = run(tmp_path, fake, "klines_1m", date(2024, 2, 1), date(2024, 2, 29))

    [row] = report.files
    assert row.rows == 3
    assert row.missing_minutes == 29 * 1440 - 3
    root = tmp_path / "data/normalized/klines_1m/symbol=BTCUSDT"
    first = pq.read_table(root / "date=2024-02-01/archive.parquet")
    assert first.column("open_ts_us").to_pylist() == [feb_1_ms * 1000, (feb_1_ms + 60_000) * 1000]
    second = pq.read_table(root / "date=2024-02-02/archive.parquet")
    assert second.num_rows == 1


def test_checksum_mismatch_is_rejected_and_retried_next_time(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1)])
    fake.wrong_checksum.add(TRADES_PATH)
    report, _ = run(tmp_path, fake, "trades", DAY, DAY)
    assert [f.status for f in report.files] == ["rejected"]
    assert not (tmp_path / "data/normalized").exists()

    fake.wrong_checksum.clear()
    report, _ = run(tmp_path, fake, "trades", DAY, DAY)
    assert [f.status for f in report.files] == ["imported"]


def test_rerun_skips_imported_files(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1)])
    run(tmp_path, fake, "trades", DAY, DAY)
    report, _ = run(tmp_path, fake, "trades", DAY, DAY)
    assert (report.skipped, report.files) == (1, [])
    assert fake.calls[TRADES_PATH] == 1


def test_interrupted_file_is_redone_without_duplicates(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1), trade_line(2)])
    replica = tmp_path / "replica"

    class FailingStore(LocalDirectoryStore):
        def put(self, local: Path, key: str, sha256: str) -> None:
            raise StorageError("S3 indisponível")

    with pytest.raises(StorageError):
        run(tmp_path, fake, "trades", DAY, DAY, store=FailingStore(replica))
    report, _ = run(tmp_path, fake, "trades", DAY, DAY, store=LocalDirectoryStore(replica))

    [row] = report.files
    assert row.status == "imported"
    key = "normalized/trades/symbol=BTCUSDT/date=2026-10-22/archive.parquet"
    assert row.outputs == (key,)
    assert pq.read_table(replica / key).column("trade_id").to_pylist() == [1, 2]
    assert not (tmp_path / "data" / key).exists(), "com réplica, a cópia local é apagada"


def test_missing_file_and_retries(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1)])
    fake.failures[TRADES_PATH] = [429, 503]
    report, sleeps = run(tmp_path, fake, "trades", DAY, date(2026, 10, 23))
    assert [f.status for f in report.files] == ["imported", "missing"]
    assert sleeps[0] == 7.0, "espera o Retry-After do 429"
    assert len(sleeps) == 2


def test_persistent_failure_stops_the_run(tmp_path: Path) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1)])
    fake.failures[TRADES_PATH + ".CHECKSUM"] = [500] * 5
    with pytest.raises(ArchiveError):
        run(tmp_path, fake, "trades", DAY, DAY)


def test_cli_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeArchive()
    fake.add(TRADES_PATH, [trade_line(1), trade_line(2)])
    real_client = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr("trader.cli.main.httpx.Client", client)
    args = ["backfill", "--data-dir", str(tmp_path), "--dataset", "trades"]
    assert main([*args, "--start", "2026-10-22", "--end", "2026-10-22"]) == 0
    out = capsys.readouterr().out
    assert "importados 1" in out and "linhas: 2" in out
    with StateStore(tmp_path / "state.db") as state:
        assert state.backfill_status("BTCUSDT-trades-2026-10-22.zip") == "imported"


def test_archive_file_names() -> None:
    daily = ArchiveFile("klines_1m", "BTCUSDT", False, DAY, DAY)
    assert daily.url_path == "/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-22.zip"
