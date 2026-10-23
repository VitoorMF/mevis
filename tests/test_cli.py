import time
from pathlib import Path

import pytest

from trader.cli.main import main
from trader.ops.health import health_path, write_health


def test_status_without_health_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status", "--data-dir", str(tmp_path)]) == 1
    assert "sem arquivo de saúde" in capsys.readouterr().out


def test_status_with_fresh_health_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    now = time.time_ns() // 1000
    write_health(
        health_path(tmp_path),
        {
            "status": "running",
            "version": "0.1.0",
            "updated_ts_us": now,
            "primary_session": "S1",
            "primary_endpoint": "wss://x",
            "channels": {"btcusdt@trade": {"messages": 10, "last_recv_ts_us": now}},
            "gaps_detected": 0,
            "reconnects": 1,
            "malformed_messages": 0,
            "clock_offset_us": 480_000,
        },
    )
    assert main(["status", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "btcusdt@trade" in out
    assert "+480 ms" in out


def test_stale_health_file_is_not_healthy(tmp_path: Path) -> None:
    old = time.time_ns() // 1000 - 120 * 1_000_000
    write_health(
        health_path(tmp_path),
        {
            "status": "running",
            "version": "0.1.0",
            "updated_ts_us": old,
            "primary_session": None,
            "primary_endpoint": None,
            "channels": {},
            "gaps_detected": 0,
            "reconnects": 0,
            "malformed_messages": 0,
            "clock_offset_us": None,
        },
    )
    assert main(["status", "--data-dir", str(tmp_path)]) == 1


def test_invalid_config_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[collector]\nnope = 1\n")
    assert main(["status", "--config", str(config)]) == 2
    assert "configuração inválida" in capsys.readouterr().err


def test_status_lists_every_instance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    now = time.time_ns() // 1000
    for instance in ("a", "b"):
        write_health(
            health_path(tmp_path, instance),
            {
                "status": "running",
                "instance": instance,
                "version": "0.1.0",
                "updated_ts_us": now,
                "primary_session": "S",
                "primary_endpoint": "wss://x",
                "channels": {},
                "gaps_detected": 0,
                "reconnects": 0,
                "malformed_messages": 0,
                "clock_offset_us": None,
            },
        )
    assert main(["status", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "coletor [a]" in out and "coletor [b]" in out
