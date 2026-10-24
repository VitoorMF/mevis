"""Relatório de qualidade sobre estado sintético (doc 06 §8.3; RF-158)."""

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from trader.cli.main import main
from trader.ops.state import GapRecord, RawFileRow, StateStore
from trader.pipeline.quality_report import _summary, build_report

DAY = date(2026, 10, 24)
DAY_US = 1_792_800_000_000_000  # 24/10/2026 00:00 UTC
HOUR_US = 3_600_000_000


def test_percentiles_from_histogram() -> None:
    stats = _summary(Counter({-3: 1, 10: 50, 20: 40, 70: 8, 900: 1}))
    assert stats.count == 100
    assert stats.negative == 1
    assert stats.max_ms == 900
    assert stats.percentiles_ms == {"50": 10, "90": 20, "99": 70, "99.9": 900}


def test_state_sections(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.db") as state:
        # lacuna que começa antes do dia: só 60 s contam dentro do período
        gap = GapRecord("btcusdt@trade", DAY_US - 30_000_000, DAY_US + 60_000_000, "x", 1, True)
        state.record_gap(gap)
        state.session_opened("S1", "wss://x", ["a"], DAY_US + 10, "v")
        state.session_closed("S1", DAY_US + 20, "handover", 1)
        state.session_opened("S2", "wss://x", ["a"], DAY_US + 30, "v")
        state.session_opened("S0", "wss://x", ["a"], DAY_US - 10, "v")  # fora do período
        for i, (offset, rtt) in enumerate([(-1_000, 138_000), (2_000, 140_000), (500, 139_000)]):
            state.record_clock_offset(DAY_US + i, DAY_US + i + offset, rtt, offset)
        state.set_hour_stats("BTCUSDT", DAY_US // HOUR_US, {("rows", "trades"): 7})
        state.register_raw_file(
            RawFileRow("raw/a.zst", "c", "S1", DAY_US // HOUR_US, "h", "f", 1000, 100, 1)
        )
        report = build_report(tmp_path, state, "BTCUSDT", DAY, DAY)

    assert report.gaps == {"btcusdt@trade open": {"count": 1, "seconds": 60.0}}
    assert report.sessions == {"handover": 1, "aberta": 1}
    assert report.clock["samples"] == 3
    assert report.clock["median_ms"] == 0.5
    assert report.clock["max_abs_ms"] == 2.0
    assert report.rows == {"trades": 7}
    assert report.storage == {"2026-10-24": {"compressed": 100, "original": 1000}}
    assert len(report.hours_without_mask) == 24
    assert report.delays["trades"].count == 0


def test_cli_quality_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    StateStore(tmp_path / "state.db").close()
    out = tmp_path / "q.json"
    args = ["quality", "--data-dir", str(tmp_path), "--start", "2026-10-24", "--end", "2026-10-24"]
    assert main([*args, "--json", str(out)]) == 0
    assert "período: 2026-10-24 a 2026-10-24" in capsys.readouterr().out
    assert json.loads(out.read_text())["hours"] == 24
