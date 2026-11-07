"""Página pública de status (doc 03 §7.7, ADR-13; regras em doc 06 §8.5)."""

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from test_pipeline import END, START, build_scenario, run

from conftest import FakeS3
from trader.cli.main import main
from trader.ops.config import PipelineConfig
from trader.ops.health import health_path, write_health
from trader.ops.state import StateStore
from trader.pipeline.public_status import StatusPublisher
from trader.storage.layout import HOUR_US

SECOND = 1_000_000
MINUTE = 60 * SECOND
STREAMS = ("btcusdt@trade", "btcusdt@kline_1m", "btcusdt@depth20@100ms", "btcusdt@depth@100ms")
FORBIDDEN_KEYS = {
    "price", "qty", "quote_qty", "volume", "bid_px", "ask_px", "trade_id", "first_missing_id",
    "last_missing_id", "endpoint", "primary_endpoint", "session_id", "instance", "bucket",
}  # fmt: skip


class RecordingSink:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str, str]] = []

    def put(self, key: str, body: bytes, content_type: str, cache_control: str) -> None:
        self.puts.append((key, body, content_type, cache_control))

    def keys(self) -> list[str]:
        return [key for key, *_ in self.puts]

    def last(self, key: str) -> Any:
        return json.loads(next(body for k, body, *_ in reversed(self.puts) if k == key))


def health(
    instance: str,
    started: int,
    updated: int,
    trades: int,
    sessions: dict[str, int],
    status: str = "running",
) -> dict[str, Any]:
    channels = {s: {"messages": trades, "last_recv_ts_us": updated - 200_000} for s in STREAMS}
    return {
        "status": status,
        "instance": instance,
        "version": "0.1.0",
        "updated_ts_us": updated,
        "started_ts_us": started,
        "primary_session": next(iter(sessions), None),
        "primary_endpoint": "wss://data-stream.binance.vision",
        "open_sessions": list(sessions),
        "session_messages": sessions,
        "channels": channels,
        "gaps_detected": 0,
        "reconnects": 0,
        "malformed_messages": 0,
        "clock_offset_us": 4300,
    }


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def test_counter_and_rates_across_instance_swap(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    t0 = START + HOUR_US
    with StateStore(data_dir / "state.db") as state:
        state.session_opened("S1", "wss://x", STREAMS, START, "v")
        state.session_closed("S1", START + 10 * MINUTE, "rotation", 1000)
        state.session_opened("S2", "wss://x", STREAMS, START + 10 * MINUTE, "v")
        state.session_closed("S2", START + 20 * MINUTE, "silence:btcusdt@trade", 500)
        state.session_opened("S3", "wss://x", STREAMS, START + 20 * MINUTE, "v")
    publisher = StatusPublisher(PipelineConfig(data_dir=data_dir), RecordingSink())

    write_health(health_path(data_dir, "a"), health("a", START, t0, 100, {"S3": 50}))
    first = publisher.status(t0)
    assert first["messages"] == {"received": 1550, "rate": None}
    assert first["sessions"] == {"total": 3, "reconnects": 1}
    assert first["collection_start_us"] == START
    assert first["series"] == []

    # Troca a/b: a nova instância começa do zero e não entra na taxa até ter duas leituras.
    t1 = t0 + 15 * SECOND
    write_health(health_path(data_dir, "a"), health("a", START, t1, 400, {"S3": 350}))
    write_health(health_path(data_dir, "b"), health("b", t0 + 10 * SECOND, t1, 50, {"S4": 10}))
    second = publisher.status(t1)
    assert second["messages"]["received"] == 1500 + 350 + 10
    assert second["messages"]["rate"] == round((1860 - 1550) / 15, 1)
    assert [c["rate"] for c in second["channels"]] == [20.0] * 4
    assert [c["age_s"] for c in second["channels"]] == [0.2] * 4
    assert [c["id"] for c in second["channels"]] == ["trade", "kline_1m", "depth20@100ms",
                                                     "depth@100ms"]  # fmt: skip
    assert second["collector"] == {"state": "running", "since_us": START, "version": "0.1.0"}
    assert second["clock_offset_ms"] == 4.3
    assert second["series"] == [{"t": t1, "total": second["messages"]["rate"],
                                 "rates": {c["id"]: 20.0 for c in second["channels"]}}]  # fmt: skip


def test_collector_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    publisher = StatusPublisher(PipelineConfig(data_dir=data_dir), RecordingSink())
    now = START + HOUR_US
    assert publisher.status(now)["collector"]["state"] == "unknown"

    write_health(health_path(data_dir, "a"), health("a", START, now - 60 * SECOND, 1, {}))
    assert publisher.status(now)["collector"]["state"] == "stale"

    stopped = health("a", START, now, 1, {}, status="stopped")
    write_health(health_path(data_dir, "a"), stopped)
    status = publisher.status(now)
    assert status["collector"]["state"] == "stopped"
    assert all(c["age_s"] is None for c in status["channels"])


def test_publishes_only_operational_metrics(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    now = END + 2 * HOUR_US
    run(PipelineConfig(data_dir=data_dir), tmp_path / "replica", now)
    write_health(health_path(data_dir, "a"), health("a", START, now, 10, {"S9": 3}))
    bucket = "trader-data-123456789012-us-east-2"
    config = PipelineConfig(data_dir=data_dir, replica_s3_bucket=bucket)
    sink = RecordingSink()
    StatusPublisher(config, sink).tick(now)

    status, history = sink.last("status.json"), sink.last("history.json")
    [gap] = status["gaps"]["recent"]
    assert gap == {
        "channel": "Negócios",
        "cause": "salto na sequência de negócios",
        "status": "open",
        "start_us": START + 30 * MINUTE,
        "duration_s": 0.0,
    }
    assert status["storage"]["raw_stored_bytes"] < status["storage"]["raw_received_bytes"]
    for document in (status, history):
        assert not keys_of(document) & FORBIDDEN_KEYS
        text = json.dumps(document)
        assert "wss://" not in text and bucket not in text and "123456789012" not in text


def test_history_skips_minutes_before_the_collection(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    now = END + 2 * HOUR_US
    run(PipelineConfig(data_dir=data_dir), tmp_path / "replica", now)
    db = sqlite3.connect(data_dir / "state.db")
    db.execute("UPDATE sessions SET opened_ts_us = ?", (START + 30 * MINUTE,))
    db.commit()
    db.close()

    history = StatusPublisher(PipelineConfig(data_dir=data_dir), RecordingSink()).history(now)

    assert history["collection_start_us"] == START + 30 * MINUTE
    by_hour = {h["h"] * SECOND: h for h in history["hours"]}
    hour = by_hour[START]
    assert hour["rows"]["trades"] > 0
    assert sum(hour["minutes"]["trade"]) == 30  # só os minutos depois do início
    p50, p99 = hour["delay_ms"]
    assert 0 < p50 <= p99
    assert "minutes" not in by_hour.get(START - HOUR_US, {})


def test_tick_publishes_page_once_and_history_when_it_changes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    now = END + 2 * HOUR_US
    run(PipelineConfig(data_dir=data_dir), tmp_path / "replica", now)
    sink = RecordingSink()
    publisher = StatusPublisher(PipelineConfig(data_dir=data_dir), sink)

    publisher.tick(now)
    publisher.tick(now + 15 * SECOND)
    publisher.tick(now + 301 * SECOND)  # histórico conferido de novo, sem mudança
    assert sink.keys() == ["index.html", "history.json", "status.json", "status.json",
                           "status.json"]  # fmt: skip
    with StateStore(data_dir / "state.db") as state:
        state.set_hour_stats("BTCUSDT", START // HOUR_US, {("rows", "trades"): 99})
    publisher.tick(now + 602 * SECOND)
    assert sink.keys()[-2:] == ["history.json", "status.json"]

    headers = {key: (ctype, cache) for key, _, ctype, cache in sink.puts}
    assert headers["status.json"] == ("application/json", "public, max-age=10")
    assert headers["history.json"] == ("application/json", "public, max-age=300")
    assert headers["index.html"] == ("text/html; charset=utf-8", "public, max-age=60")
    page = sink.puts[0][1].decode()
    assert "Dados de mercado: Binance" in page and "status.json" in page


def test_bucket_totals_and_backfill_counts_from_s3(tmp_path: Path) -> None:
    rows = [{"dataset": "trades", "status": "imported", "rows": 150, "first_day": "2025-10-23",
             "last_day": "2025-10-24"}]  # fmt: skip
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buffer)
    s3 = FakeS3(
        {
            "derived/ops/backfill_files-mac.parquet": buffer.getvalue(),
            "raw/binance-spot/btcusdt/x.jsonl.zst": b"12345",
        }
    )
    config = PipelineConfig(data_dir=tmp_path / "data", replica_s3_bucket="bucket")
    sink = RecordingSink()
    StatusPublisher(config, sink, s3).tick(START)

    status = sink.last("status.json")
    assert status["storage"]["bucket_objects"] == 2
    assert status["storage"]["bucket_bytes"] == len(buffer.getvalue()) + 5
    assert status["history"] == {
        "trades": {"files": 1, "rows": 150, "first_day": "2025-10-23", "last_day": "2025-10-24"}
    }


def test_cli_writes_to_a_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_scenario(data_dir)
    out = tmp_path / "site"
    assert main(["status-publisher", "--data-dir", str(data_dir), "--out", str(out), "--once"]) == 0
    assert {p.name for p in out.iterdir()} == {"index.html", "status.json", "history.json"}
    assert main(["status-publisher", "--data-dir", str(data_dir), "--once"]) == 2
