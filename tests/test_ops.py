import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from trader.ingest.clockprobe import measure_offset
from trader.ops.config import CollectorConfig, ConfigError, load_config
from trader.ops.health import read_health, write_health
from trader.ops.ids import new_session_id
from trader.ops.logs import JsonFormatter, ctx
from trader.ops.state import GapRecord, StateStore
from trader.sources.binance_rest import RestResponse


class TestIds:
    def test_format(self) -> None:
        session = new_session_id(1_792_779_042_655)
        assert len(session) == 26
        assert set(session) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    def test_ordered_by_time(self) -> None:
        assert new_session_id(1_000) < new_session_id(1_001)


class TestConfig:
    def test_defaults_are_valid(self) -> None:
        config = CollectorConfig().validate()
        assert config.streams == (
            "btcusdt@trade",
            "btcusdt@kline_1m",
            "btcusdt@depth20@100ms",
            "btcusdt@depth@100ms",
        )
        assert config.silence_limit_s("btcusdt@depth20@100ms") == 2.0

    def test_load_from_toml_with_overrides(self, tmp_path: Path) -> None:
        path = tmp_path / "trader.toml"
        path.write_text(
            '[collector]\nrotation_after_s = 3600\nsilence_limits_s = { "trade" = 60 }\n'
        )
        config = load_config(path, data_dir=tmp_path / "d", symbol=None)
        assert config.rotation_after_s == 3600
        assert config.silence_limits_s["trade"] == 60
        assert config.silence_limits_s["depth@100ms"] == 2.0
        assert config.data_dir == tmp_path / "d"

    def test_unknown_key(self, tmp_path: Path) -> None:
        path = tmp_path / "trader.toml"
        path.write_text("[collector]\nrotaton_after_s = 1\n")
        with pytest.raises(ConfigError, match="desconhecidas"):
            load_config(path)

    @pytest.mark.parametrize(
        "changes",
        [
            {"symbol": "btcusdt"},
            {"ws_base_urls": ("https://x",)},
            {"rest_weight_budget_per_min": 7000},
            {"flush_interval_s": 0},
            {"snapshot_limit": 6000},
            {"silence_limits_s": {"trade": 1.0}},
            {"backoff_initial_s": 100.0},
        ],
    )
    def test_invalid_values(self, changes: dict[str, object]) -> None:
        with pytest.raises(ConfigError):
            replace(CollectorConfig(), **changes).validate()  # type: ignore[arg-type]


class TestState:
    def test_sessions_and_gaps(self, tmp_path: Path) -> None:
        with StateStore(tmp_path / "state.db") as state:
            state.session_opened("S1", "wss://x", ["a", "b"], 100, "v1")
            state.session_closed("S1", 200, "rotation", 42)
            gap = GapRecord("btcusdt@trade", 10, 20, "trade_id_jump", 150, True, 5, 9)
            state.record_gap(gap)
            state.record_gap(replace(gap, recoverable=False, detected_ts_us=50))
            [session] = state.sessions()
            assert (session.close_reason, session.messages) == ("rotation", 42)
            [stored] = state.gaps_since(100)
            assert stored.record == gap
            assert stored.status == "open"
            assert len(state.gaps_since(0)) == 2

    def test_watermarks_survive_reopening(self, tmp_path: Path) -> None:
        with StateStore(tmp_path / "state.db") as state:
            state.save_watermarks({"BTCUSDT:trade": (7, 70), "BTCUSDT:kline": (None, 60)})
            state.record_clock_offset(1, 2, 3, 4)
        with StateStore(tmp_path / "state.db") as state:
            assert state.load_watermarks() == {
                "BTCUSDT:trade": (7, 70),
                "BTCUSDT:kline": (None, 60),
            }


def test_measure_offset() -> None:
    response = RestResponse(
        channel="rest:time",
        sent_ts_us=1_792_779_000_000_000,
        recv_ts_us=1_792_779_000_600_000,
        status=200,
        body=b'{"serverTime":1792779000780000}',
    )
    offset = measure_offset(response)
    assert offset.rtt_us == 600_000
    assert offset.local_ts_us == 1_792_779_000_300_000
    assert offset.offset_us == 480_000


def test_health_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "health" / "collector.json"
    assert read_health(path) is None
    write_health(path, {"status": "running"})
    assert read_health(path) == {"status": "running"}


def test_json_log_lines_include_context() -> None:
    record = logging.LogRecord("trader", logging.WARNING, __file__, 1, "lacuna", None, None)
    for key, value in ctx(channel="btcusdt@trade").items():
        setattr(record, key, value)
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "lacuna"
    assert entry["level"] == "WARNING"
    assert entry["channel"] == "btcusdt@trade"


def test_instance_name_is_validated() -> None:
    assert CollectorConfig(instance="b").validate().instance == "b"
    with pytest.raises(ConfigError):
        CollectorConfig(instance="b/../x").validate()


def test_pipeline_config_inherits_collector_settings(tmp_path: Path) -> None:
    from trader.ops.config import load_pipeline_config

    path = tmp_path / "trader.toml"
    path.write_text(
        '[collector]\ndata_dir = "/var/lib/trader"\nstatsd_host = "127.0.0.1"\n'
        '[pipeline]\nreplica_s3_bucket = "b"\naws_region = "ap-northeast-1"\n'
    )
    config = load_pipeline_config(path)
    assert config.data_dir == Path("/var/lib/trader")
    assert config.statsd_host == "127.0.0.1"
    assert config.replica_s3_bucket == "b"
