"""Pipeline horário de ponta a ponta sobre arquivos brutos sintéticos (doc 03 §6.3)."""

import fcntl
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
import zstandard

from trader.core.rawformat import RawRecord, encode
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.ingest.raw_writer import RawWriter
from trader.ops.config import PipelineConfig
from trader.ops.state import GapRecord, StateStore
from trader.pipeline.hourly import HourlyPipeline, PipelineBusy
from trader.pipeline.quality_report import build_report
from trader.storage.layout import HOUR_US, dataset_path, hour_dirs, quarantine_path, raw_root
from trader.storage.objectstore import LocalDirectoryStore

START = 1_792_764_000_000_000  # 2026-10-23 14:00 UTC
HOUR = START // HOUR_US
END = START + HOUR_US
TRADE, KLINE, TOP20, DIFF = (
    "btcusdt@trade",
    "btcusdt@kline_1m",
    "btcusdt@depth20@100ms",
    "btcusdt@depth@100ms",
)


def message(stream: str, data: dict[str, Any]) -> bytes:
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":")).encode()


def trade(trade_id: int, ts: int, price: str = "81524.01000000") -> bytes:
    data = {
        "e": "trade",
        "E": ts,
        "s": "BTCUSDT",
        "t": trade_id,
        "p": price,
        "q": "0.00008000",
        "T": ts,
        "m": False,
        "M": True,
    }
    return message(TRADE, data)


def book(update_id: int, bid: str = "81524.00000000", *, unsorted: bool = False) -> bytes:
    asks = [["81524.01000000", "1.00000000"], ["81525.00000000", "1.00000000"]]
    data = {
        "lastUpdateId": update_id,
        "bids": [[bid, "1.00000000"], ["81523.00000000", "1.00000000"]],
        "asks": asks[::-1] if unsorted else asks,
    }
    return message(TOP20, data)


def diff(first: int, last: int, ts: int) -> bytes:
    data = {"e": "depthUpdate", "E": ts, "s": "BTCUSDT", "U": first, "u": last, "b": [], "a": []}
    return message(DIFF, data)


def closed_kline(open_ts: int) -> bytes:
    k = {
        "t": open_ts,
        "T": open_ts + MINUTE_US - 1,
        "s": "BTCUSDT",
        "i": "1m",
        "f": 1,
        "L": 10,
        "o": "81520.00000000",
        "c": "81524.01000000",
        "h": "81530.00000000",
        "l": "81510.00000000",
        "v": "1.00000000",
        "n": 10,
        "x": True,
        "q": "81520.00000000",
        "V": "0.50000000",
        "Q": "40760.00000000",
        "B": "0",
    }
    return message(KLINE, {"e": "kline", "E": open_ts + MINUTE_US, "s": "BTCUSDT", "k": k})


class RawBuilder:
    def __init__(self, data_dir: Path) -> None:
        self.writer = RawWriter(data_dir, "BTCUSDT")
        self.seq: dict[str, int] = {}

    def add(self, session: str, channel: str, recv: int, payload: bytes) -> None:
        self.seq[session] = self.seq.get(session, 0) + 1
        self.writer.write(RawRecord(recv, session, self.seq[session], channel, payload))


def build_scenario(data_dir: Path) -> None:
    raw = RawBuilder(data_dir)
    s = SECOND_US
    # Sessão S1, hora H: negócios 1..10, livro, diferencial e o candle do minuto 0.
    for i in range(1, 11):
        raw.add("S1", TRADE, START + i * s + 1000, trade(i, START + i * s))
    raw.add("S1", TOP20, START + 1 * s, book(100))
    raw.add("S1", TOP20, START + 2 * s, book(101, bid="81524.05000000"))  # cruzado: fica, inválido
    raw.add("S1", TOP20, START + 3 * s, book(102, unsorted=True))  # desordenado: quarentena
    raw.add("S1", DIFF, START + 1 * s, diff(1, 5, START + 1 * s))
    raw.add("S1", KLINE, START + 60 * s + 500_000, closed_kline(START))
    raw.add("S1", TRADE, START + 20 * s, b'{"stream":"btcusdt@trade","data":{"e":"trade"}}')
    # Negócio da hora anterior recebido já na hora H, e o candle do último minuto de H-1.
    raw.add("S1", TRADE, START + 100_000, trade(0, START - 200_000))
    raw.add("S1", KLINE, START + 500_000, closed_kline(START - MINUTE_US))
    # Sessão S2 sobreposta: cópias iguais de 5..10, exceto 7, que chega com outro preço.
    for i in range(5, 11):
        price = "99999.00000000" if i == 7 else "81524.01000000"
        raw.add("S2", TRADE, START + i * s + 5000, trade(i, START + i * s, price))
    # Negócio do fim de H recebido no começo de H+1.
    raw.add("S1", TRADE, END + 200_000, trade(11, END - 100_000))
    raw.writer.close()

    # Sessão S3 caiu na hora H-1 sem fechar o arquivo; a última linha ficou truncada.
    date_dir, hour_dir = hour_dirs(HOUR - 1)
    orphan = raw_root(data_dir, "BTCUSDT") / TRADE / date_dir / hour_dir / "S3.jsonl.open"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    good = encode(RawRecord(START - 10 * s, "S3", 1, TRADE, trade(-1, START - 10 * s)))
    orphan.write_bytes(good + b'{"r":1,"s":"S3","q":2,"c":"btcusdt@trade","m":{"str')

    with StateStore(data_dir / "state.db") as state:
        for session in ("S1", "S2", "S3"):
            state.session_opened(session, "wss://x", [TRADE], START - HOUR_US, "test")
        for session in ("S1", "S2"):
            state.session_closed(session, END + HOUR_US, "shutdown", 0)
        for minute in range(-60, 121, 5):
            local = START + minute * MINUTE_US
            state.record_clock_offset(local, local + 100_000, 50_000, 100_000)
        state.record_gap(
            GapRecord(
                TRADE,
                START + 30 * MINUTE_US,
                START + 30 * MINUTE_US + 10,
                "trade_id_jump",
                START + 31 * MINUTE_US,
                True,
                1000,
                1001,
            )
        )


def rows(path: Path) -> list[dict[str, Any]]:
    return list(pq.read_table(path).to_pylist())


def quarantine_reasons(data_dir: Path, hour: int) -> list[str]:
    content = zstandard.ZstdDecompressor().decompress(
        quarantine_path(data_dir, "BTCUSDT", hour).read_bytes(), max_output_size=10**7
    )
    return [json.loads(line)["reason"] for line in content.splitlines()]


@pytest.fixture
def scenario(tmp_path: Path) -> tuple[PipelineConfig, Path]:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    return PipelineConfig(data_dir=data_dir), tmp_path / "replica"


def run(config: PipelineConfig, replica: Path, now_us: int, **kwargs: Any) -> Any:
    with StateStore(config.data_dir / "state.db") as state:
        return HourlyPipeline(config, state, LocalDirectoryStore(replica)).run(now_us, **kwargs)


def test_end_to_end(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    data_dir = config.data_dir
    report = run(config, replica, END + 2 * HOUR_US)

    assert report.sealed_orphans == 1
    assert report.compressed == 7  # 4 canais de S1 em H, S2 em H, S1 em H+1 e o órfão em H-1
    remaining = {p.name.split(".", 1)[1] for p in raw_root(data_dir, "BTCUSDT").rglob("S*")}
    assert remaining == {"jsonl.zst"}
    assert set(report.normalized_hours) == {HOUR - 1, HOUR, HOUR + 1}

    trades = rows(dataset_path(data_dir, "normalized", "trades", "BTCUSDT", HOUR))
    assert sorted(t["trade_id"] for t in trades) == list(range(1, 12))
    seven = next(t for t in trades if t["trade_id"] == 7)
    assert seven["price"] == Decimal("81524.01")  # primeira cópia recebida
    assert seven["provenance"] == "S1"
    assert report.duplicates[TRADE] == 5

    previous = rows(dataset_path(data_dir, "normalized", "trades", "BTCUSDT", HOUR - 1))
    assert sorted(t["trade_id"] for t in previous) == [-1, 0]

    klines = rows(dataset_path(data_dir, "normalized", "klines_1m", "BTCUSDT", HOUR))
    assert [k["open_ts_us"] for k in klines] == [START]
    previous_klines = rows(dataset_path(data_dir, "normalized", "klines_1m", "BTCUSDT", HOUR - 1))
    assert [k["open_ts_us"] for k in previous_klines] == [START - MINUTE_US]

    books = rows(dataset_path(data_dir, "normalized", "book_top20", "BTCUSDT", HOUR))
    assert [b["last_update_id"] for b in books] == [100, 101]
    assert books[0]["bid_px"] == [Decimal("81524"), Decimal("81523")]

    reasons = quarantine_reasons(data_dir, HOUR)
    assert "cópia com conteúdo diferente" in reasons
    assert "book_unsorted" in reasons
    assert any("trade" in r and "KeyError" in r for r in reasons)
    assert quarantine_reasons(data_dir, HOUR - 1) == ["linha truncada"]

    mask = {
        (r["channel"], (r["minute_ts_us"] - START) // MINUTE_US): r["status"]
        for r in rows(dataset_path(data_dir, "derived", "quality_mask", "BTCUSDT", HOUR))
    }
    assert mask[(TRADE, 0)] == "invalid"  # cópia conflitante do negócio 7 e mensagem malformada
    assert mask[(TRADE, 30)] == "missing"  # lacuna aberta
    assert mask[(TOP20, 0)] == "invalid"  # livro cruzado e livro desordenado
    assert mask[(DIFF, 0)] == "ok"
    assert mask[(KLINE, 0)] == "ok"
    assert mask[(KLINE, 1)] == "missing"

    replicated = {p.relative_to(replica).as_posix() for p in replica.rglob("*") if p.is_file()}
    assert any(k.endswith("S1.jsonl.zst") for k in replicated)
    assert any(k.startswith("normalized/trades/") for k in replicated)
    assert "derived/ops/gaps.parquet" in replicated
    assert "ops/state-backup.db" in replicated


def test_second_run_is_idempotent(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    run(config, replica, END + 2 * HOUR_US)
    again = run(config, replica, END + 2 * HOUR_US)
    assert (again.sealed_orphans, again.compressed, again.normalized_hours) == (0, 0, [])


def test_new_neighbor_file_triggers_renormalization(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    run(config, replica, END + 2 * HOUR_US)
    late = RawBuilder(config.data_dir)
    late.add("S4", TRADE, END + 300_000, trade(12, END - 50_000))
    late.writer.close()
    report = run(config, replica, END + 2 * HOUR_US)
    assert HOUR in report.normalized_hours
    trades = rows(dataset_path(config.data_dir, "normalized", "trades", "BTCUSDT", HOUR))
    assert 12 in {t["trade_id"] for t in trades}


def test_retention_only_deletes_replicated_old_files(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    run(config, replica, END + 2 * HOUR_US)
    later = run(config, replica, END + 10 * 24 * HOUR_US)
    assert later.deleted > 0
    assert not list(raw_root(config.data_dir, "BTCUSDT").rglob("*.zst"))
    assert any(replica.rglob("*.jsonl.zst"))
    # Sem as entradas locais, a hora não é refeita nem com --rebuild.
    rebuilt = run(config, replica, END + 10 * 24 * HOUR_US, rebuild=True)
    assert rebuilt.normalized_hours == []


def test_without_replica_nothing_is_deleted(scenario: tuple[PipelineConfig, Path]) -> None:
    config, _ = scenario
    with StateStore(config.data_dir / "state.db") as state:
        pipeline = HourlyPipeline(replace(config, local_retention_days=1), state, None)
        report = pipeline.run(END + 10 * 24 * HOUR_US)
    assert report.deleted == 0
    assert any(raw_root(config.data_dir, "BTCUSDT").rglob("*.zst"))


def test_concurrent_runs_are_rejected(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    with (config.data_dir / "pipeline.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PipelineBusy):
            run(config, replica, END + 2 * HOUR_US)


def test_replication_lag_metric(scenario: tuple[PipelineConfig, Path]) -> None:
    config, replica = scenario
    report = run(config, replica, END + 2 * HOUR_US)
    assert report.replication_lag_minutes == 0.0
    with StateStore(config.data_dir / "state.db") as state:
        without_replica = HourlyPipeline(config, state, None).run(END + 2 * HOUR_US)
    assert without_replica.replication_lag_minutes is None


def test_quality_report_matches_pipeline_output(scenario: tuple[PipelineConfig, Path]) -> None:
    """CA-05: o relatório do dia bate com o que o pipeline normalizou (doc 06 §8.3)."""
    config, replica = scenario
    report = run(config, replica, END + 2 * HOUR_US)
    day = date(2026, 10, 23)
    with StateStore(config.data_dir / "state.db") as state:
        quality = build_report(config.data_dir, state, "BTCUSDT", day, day)

    assert quality.rows == dict(report.rows)
    assert quality.duplicates[TRADE] == report.duplicates[TRADE] == 5
    assert sum(quality.quarantined.values()) == report.quarantined
    assert len(quality.hours_without_mask) == 24 - 3
    assert quality.mask[TRADE]["missing"] >= 1
    ratio = quality.completeness(KLINE)
    assert ratio is not None and ratio < 1
    assert quality.delays["trades"].count == report.rows["trades"]
