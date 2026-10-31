"""Conferência de continuidade entre sessões e reparo fora do disco (RF-160, doc 06 §9.2)."""

import hashlib
from pathlib import Path

import pytest
from test_pipeline import DIFF, END, HOUR, START, TOP20, TRADE, RawBuilder, diff, run, trade

from trader.cli.main import main
from trader.core.timeunits import SECOND_US
from trader.ops.config import PipelineConfig
from trader.ops.health import is_ready
from trader.ops.state import GapRecord, StateStore
from trader.pipeline.hourly import HourlyPipeline
from trader.storage.layout import HOUR_US, StorageError, dataset_path
from trader.storage.objectstore import LocalDirectoryStore

S = SECOND_US
NOW = END + 2 * HOUR_US


def swap(data_dir: Path) -> None:
    """Instância antiga para em t=10 s, e a nova só começa em t=13 s, sem lacuna registrada."""
    raw = RawBuilder(data_dir)
    for t in range(1, 11):
        raw.add("OLD", TRADE, START + t * S, trade(t, START + t * S))
        raw.add("OLD", TOP20, START + t * S, b'{"stream":"x","data":{}}')
        raw.add("OLD", DIFF, START + t * S, diff(10 * t, 10 * t + 9, START + t * S))
    for t in range(13, 21):
        raw.add("NEW", TRADE, START + t * S, trade(t, START + t * S))
        raw.add("NEW", TOP20, START + t * S, b'{"stream":"x","data":{}}')
        raw.add("NEW", DIFF, START + t * S, diff(10 * t, 10 * t + 9, START + t * S))
    raw.writer.close()
    with StateStore(data_dir / "state.db") as state:
        for session in ("OLD", "NEW"):
            state.session_opened(session, "wss://x", [TRADE], START, "t")
            state.session_closed(session, END, "shutdown", 0)


def gaps(data_dir: Path) -> dict[str, GapRecord]:
    with StateStore(data_dir / "state.db") as state:
        return {g.record.channel: g.record for g in state.gaps_since(0)}


def test_hole_between_sessions_becomes_gap_and_is_not_duplicated(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    swap(data_dir)
    config = PipelineConfig(data_dir=data_dir)

    report = run(config, tmp_path / "replica", NOW)
    assert report.continuity_gaps == 3

    found = gaps(data_dir)
    assert (found[TRADE].first_missing_id, found[TRADE].last_missing_id) == (11, 12)
    assert found[TRADE].recoverable and found[TRADE].cause == "trade_id_jump"
    assert (found[DIFF].first_missing_id, found[DIFF].last_missing_id) == (110, 129)
    assert not found[DIFF].recoverable
    assert (found[TOP20].start_ts_us, found[TOP20].end_ts_us) == (START + 10 * S, START + 13 * S)

    assert run(config, tmp_path / "replica", NOW).continuity_gaps == 0
    with StateStore(data_dir / "state.db") as state:
        assert len(state.gaps_since(0)) == 3


def test_overlapping_sessions_leave_no_gap(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw = RawBuilder(data_dir)
    for t in range(1, 11):
        raw.add("OLD", TRADE, START + t * S, trade(t, START + t * S))
    for t in range(8, 21):
        raw.add("NEW", TRADE, START + t * S + 1, trade(t, START + t * S))
    raw.writer.close()
    assert run(PipelineConfig(data_dir=data_dir), tmp_path / "r", NOW).continuity_gaps == 0


def test_hole_at_the_hour_boundary_and_known_gap(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw = RawBuilder(data_dir)
    raw.add("A", TRADE, END - S, trade(1, END - S))
    raw.add("B", TRADE, END + S, trade(5, END + S))
    raw.add("B", TRADE, END + 2 * S, trade(6, END + 2 * S))
    raw.add("B", TRADE, END + 3 * S, trade(9, END + 3 * S))
    raw.writer.close()
    with StateStore(data_dir / "state.db") as state:
        state.record_gap(
            GapRecord(TRADE, END + 2 * S, END + 3 * S, "trade_id_jump", END, True, 7, 8)
        )
    assert run(PipelineConfig(data_dir=data_dir), tmp_path / "r", NOW).continuity_gaps == 1
    with StateStore(data_dir / "state.db") as state:
        ids = sorted((g.record.first_missing_id, g.record.last_missing_id)
                     for g in state.gaps_since(0))  # fmt: skip
    assert ids == [(2, 4), (7, 8)]


def test_reconcile_restores_files_registers_gaps_and_applies_retention(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    replica = tmp_path / "replica"
    raw = RawBuilder(data_dir)
    for t in (1, 2, 5):
        raw.add("A", TRADE, START + t * S, trade(t, START + t * S))
    raw.writer.close()
    with StateStore(data_dir / "state.db") as state:
        state.session_opened("A", "wss://x", [TRADE], START, "t")
        state.session_closed("A", END, "shutdown", 0)
    config = PipelineConfig(data_dir=data_dir)
    old = END + 30 * 24 * HOUR_US
    # Simula a coleta anterior à conferência: já replicada e fora do disco, sem lacuna.
    with StateStore(data_dir / "state.db") as state:
        store = LocalDirectoryStore(replica)
        pipeline = HourlyPipeline(config, state, store)
        pipeline._check_continuity = lambda *_: None  # type: ignore[method-assign]
        pipeline.run(old)
        assert state.gaps_since(0) == []
        assert all(r.deleted_ts_us is not None for r in state.raw_files())

        report = HourlyPipeline(config, state, store).reconcile(HOUR, HOUR + 1, old)
        assert report.restored == 1
        assert report.continuity_gaps == 1
        [gap] = state.gaps_since(0)
        assert (gap.record.first_missing_id, gap.record.last_missing_id) == (3, 4)
        # Mask refeita e replicada; a retenção apagou o que tinha voltado ao disco.
        assert HOUR in report.normalized_hours
        assert all(r.deleted_ts_us is not None for r in state.raw_files())
    assert not list((data_dir / "raw").rglob("*.zst"))
    assert (replica / dataset_path(Path("."), "derived", "quality_mask", "BTCUSDT", HOUR)).exists()


def test_local_store_get_rejects_wrong_checksum(tmp_path: Path) -> None:
    store = LocalDirectoryStore(tmp_path / "r")
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "k").write_bytes(b"abc")
    target = tmp_path / "out" / "k"
    store.get("k", target, hashlib.sha256(b"abc").hexdigest())
    assert target.read_bytes() == b"abc"
    with pytest.raises(StorageError):
        store.get("k", tmp_path / "out" / "k2", "00" * 32)
    assert not (tmp_path / "out" / "k2").exists()


def health(**overrides: object) -> dict[str, object]:
    base = {
        "status": "running",
        "started_ts_us": 100,
        "updated_ts_us": 200,
        "channels": {"a": {"messages": 1}, "b": {"messages": 2}},
    }
    return {**base, **overrides}


@pytest.mark.parametrize(
    ("payload", "ready"),
    [
        (health(), True),
        (None, False),
        (health(status="stopped"), False),
        (health(started_ts_us=99), False),  # arquivo de uma execução anterior (doc 03 §9.2)
        (health(updated_ts_us=100), False),
        (health(channels={"a": {"messages": 1}, "b": {"messages": 0}}), False),
        (health(channels={}), False),
    ],
)
def test_is_ready(payload: dict[str, object] | None, ready: bool) -> None:
    assert is_ready(payload, since_us=100, now_us=250, max_age_us=60) is ready


def test_quality_refuses_to_run_without_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["quality", "--data-dir", str(tmp_path), "--start", "2026-10-24", "--end", "2026-10-24"]
    assert main(args) == 2
    assert "state.db" in capsys.readouterr().err
    assert not (tmp_path / "state.db").exists()


def test_reconcile_requires_a_replica(tmp_path: Path) -> None:
    period = ["--start", "2026-10-24", "--end", "2026-10-24"]
    args = ["reconcile", "--data-dir", str(tmp_path), *period]
    assert main(args) == 2
