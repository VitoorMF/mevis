from pathlib import Path

from trader.core.rawformat import RawRecord, decode
from trader.ingest.raw_writer import HOUR_US, RawWriter

HOUR_START = 1_792_764_000_000_000  # 2026-10-23 14:00:00 UTC
assert HOUR_START % HOUR_US == 0


def record(
    recv_ts_us: int, seq: int, session: str = "S1", channel: str = "btcusdt@trade"
) -> RawRecord:
    return RawRecord(recv_ts_us, session, seq, channel, b'{"n":%d}' % seq)


def files(root: Path, pattern: str) -> list[Path]:
    return sorted(root.rglob(pattern))


def test_writes_to_hourly_file_and_seals_on_close(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START + 1, 1))
    writer.write(record(HOUR_START + 2, 2))
    [open_file] = files(tmp_path, "*.open")
    expected_dir = "raw/binance-spot/btcusdt/btcusdt@trade/date=2026-10-23/hour=14"
    assert open_file.parent == tmp_path / expected_dir
    writer.close()
    assert files(tmp_path, "*.open") == []
    [sealed] = files(tmp_path, "*.jsonl")
    assert sealed.name == "S1.jsonl"
    assert [decode(line).seq for line in sealed.read_bytes().splitlines()] == [1, 2]


def test_flush_makes_data_readable_before_sealing(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START, 1))
    writer.flush()
    [open_file] = files(tmp_path, "*.open")
    assert decode(open_file.read_bytes()).payload == b'{"n":1}'
    writer.close()


def test_next_hour_seals_previous_file(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START + 10, 1))
    writer.write(record(HOUR_START + HOUR_US + 10, 2))
    [sealed] = files(tmp_path, "*.jsonl")
    [open_file] = files(tmp_path, "*.open")
    assert "hour=14" in str(sealed) and "hour=15" in str(open_file)
    writer.close()


def test_clock_going_backwards_never_reopens_a_sealed_hour(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START + 10, 1))
    writer.write(record(HOUR_START + HOUR_US + 10, 2))
    writer.write(record(HOUR_START + HOUR_US - 5, 3))  # relógio voltou para a hora 14
    writer.close()
    sealed = files(tmp_path, "*.jsonl")
    assert len(sealed) == 2
    late = next(p for p in sealed if "hour=15" in str(p))
    assert [decode(line).seq for line in late.read_bytes().splitlines()] == [2, 3]


def test_seal_stale_closes_quiet_channels_after_the_hour(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START + 10, 1))
    writer.seal_stale(HOUR_START + HOUR_US - 1)
    assert len(files(tmp_path, "*.open")) == 1
    writer.seal_stale(HOUR_START + HOUR_US)
    assert files(tmp_path, "*.open") == []


def test_seal_session_only_affects_that_session(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START, 1, session="A"))
    writer.write(record(HOUR_START, 1, session="B"))
    writer.seal_session("A")
    assert [p.name for p in files(tmp_path, "*.open")] == ["B.jsonl.open"]
    writer.close()


def test_rest_channel_directory_name(tmp_path: Path) -> None:
    writer = RawWriter(tmp_path, "BTCUSDT")
    writer.write(record(HOUR_START, 1, channel="rest:depth"))
    writer.close()
    [sealed] = files(tmp_path, "*.jsonl")
    assert "/rest_depth/" in str(sealed)
    assert decode(sealed.read_bytes()).channel == "rest:depth"
