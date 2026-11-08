"""Registro de experimentos (RF-271 a RF-275; doc 04 §11; DEC-38)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trader.experiments.registry import (
    CONFIG_KEYS,
    EXPERIMENTS,
    Registry,
    Trial,
    config_id,
    rebuild,
)
from trader.storage.objectstore import LocalDirectoryStore

NOW = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
SPEC = {"rungs": ["B4"], "horizons": [15], "model": "xgboost", "grid": {}, "seed": 0, "windows": []}


def config(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rung": "B4",
        "horizon": 15,
        "model": "xgboost",
        "hyperparameters": {"max_depth": 2, "n_estimators": 200},
        "threshold": 0.55,
        "seed": 0,
        "catalog_id": "abc",
        "label": {"fee": 0.001},
    }
    return base | changes


def registry(tmp_path: Path) -> Registry:
    store = LocalDirectoryStore(tmp_path / "s3")
    return Registry(tmp_path / "experiments" / "registry.sqlite", store)


def start(reg: Registry, at: datetime, kind: str = "model", track: str = "A", **kw: Any) -> str:
    options: dict[str, Any] = {
        "kind": kind,
        "track": track,
        "dataset_id": "d1",
        "final_evaluation": False,
        "code_version": "0.1.0+gteste",
        "who": "teste@mac",
        "spec": SPEC,
        "now": at,
    } | kw
    return reg.start(**options)


def stored(tmp_path: Path, name: str) -> dict[str, Any]:
    return dict(json.loads((tmp_path / "s3" / EXPERIMENTS / name).read_text()))


def test_config_id_identifies_the_configuration() -> None:
    assert config_id(config()) == config_id(dict(reversed(list(config().items()))))
    assert config_id(config(seed=1)) != config_id(config()), "outra semente é outra tentativa"
    assert config_id(config(threshold=0.6)) != config_id(config())
    with pytest.raises(ValueError, match="chaves"):
        config_id({k: v for k, v in config().items() if k != "seed"})
    assert set(CONFIG_KEYS) == set(config())


def test_start_registers_before_running(tmp_path: Path) -> None:
    with registry(tmp_path) as reg:
        execution_id = start(reg, NOW)
        assert execution_id == "exp-20261107T230000.000000Z"
        record = stored(tmp_path, f"{execution_id}.start.json")
        assert record["stage"] == "start" and record["spec"] == SPEC
        assert (record["track"], record["dataset_id"], record["kind"]) == ("A", "d1", "model")
        (row,) = reg.executions()
        assert row["status"] == "running" and row["finished_at"] is None
        with pytest.raises(ValueError, match="RF-271"):
            start(reg, NOW + timedelta(seconds=1), spec={"rungs": []})
        with pytest.raises(ValueError, match="tipo"):
            start(reg, NOW + timedelta(seconds=2), kind="outro")
        with pytest.raises(sqlite3.IntegrityError):
            start(reg, NOW)
        assert stored(tmp_path, f"{execution_id}.start.json") == record, "nada é substituído"


def test_finish_records_trials_and_metrics(tmp_path: Path) -> None:
    with registry(tmp_path) as reg:
        execution_id = start(reg, NOW)
        trials = [Trial(config(), {"w": {"base": {"sharpe": 1.2}}}), Trial(config(seed=1), {})]
        reg.finish(execution_id, trials=trials, now=NOW + timedelta(minutes=1))
        shown = reg.show(execution_id)
        assert shown["status"] == "ok" and shown["error"] is None
        assert {t["config_id"] for t in shown["trials"]} == {t.config_id for t in trials}
        result = stored(tmp_path, f"{execution_id}.result.json")
        assert result["status"] == "ok" and len(result["trials"]) == 2
        with pytest.raises(ValueError, match="já terminada"):
            reg.finish(execution_id, trials=[], now=NOW)


def test_error_and_interrupted_executions_stay_in_the_registry(tmp_path: Path) -> None:
    with registry(tmp_path) as reg:
        failed = start(reg, NOW)
        reg.finish(failed, trials=[], now=NOW, error="ValueError: sem preço")
        interrupted = start(reg, NOW + timedelta(seconds=1))
        statuses = {row["id"]: (row["status"], row["error"]) for row in reg.executions()}
        assert statuses == {
            failed: ("error", "ValueError: sem preço"),
            interrupted: ("running", None),
        }
        assert reg.count("A") == 0, "sem métricas, não conta"


def test_discard_is_a_mark_with_a_reason(tmp_path: Path) -> None:
    with registry(tmp_path) as reg:
        execution_id = start(reg, NOW)
        reg.finish(execution_id, trials=[Trial(config(), {})], now=NOW)
        with pytest.raises(ValueError, match="motivo"):
            reg.discard(execution_id, " ", NOW)
        reg.discard(execution_id, "defeito no cálculo", NOW + timedelta(hours=1))
        shown = reg.show(execution_id)
        assert shown["discard_reason"] == "defeito no cálculo" and len(shown["trials"]) == 1
        assert stored(tmp_path, f"{execution_id}.discard.json")["reason"] == "defeito no cálculo"
        with pytest.raises(ValueError, match="já descartada"):
            reg.discard(execution_id, "de novo", NOW)
        assert reg.count("A") == 1, "descartada depois de avaliada continua contando"
        with pytest.raises(ValueError, match="desconhecida"):
            reg.discard("exp-x", "motivo", NOW)


def test_count_uses_distinct_model_configurations(tmp_path: Path) -> None:
    with registry(tmp_path) as reg:
        first = start(reg, NOW)
        reg.finish(first, trials=[Trial(config(), {}), Trial(config(threshold=0.6), {})], now=NOW)
        again = start(reg, NOW + timedelta(seconds=1), dataset_id="d2", code_version="outra")
        reg.finish(again, trials=[Trial(config(), {})], now=NOW)  # repetição: não soma
        partial = start(reg, NOW + timedelta(seconds=2))
        reg.finish(partial, trials=[Trial(config(seed=7), {})], now=NOW, error="parou")
        baseline = start(reg, NOW + timedelta(seconds=3), kind="baseline")
        reg.finish(baseline, trials=[Trial(config(rung="B3", model="rompimento_60"), {})], now=NOW)
        other = start(reg, NOW + timedelta(seconds=4), track="B")
        reg.finish(other, trials=[Trial(config(rung="B7"), {})], now=NOW)
        assert reg.count("A") == 3, "duas configurações + a avaliada antes do erro"
        assert reg.count("B") == 1
        assert [row["id"] for row in reg.executions("B")] == [other]


def test_rebuild_from_the_stored_records(tmp_path: Path) -> None:
    store = LocalDirectoryStore(tmp_path / "s3")
    with registry(tmp_path) as reg:
        first = start(reg, NOW)
        reg.finish(first, trials=[Trial(config(), {"w": 1})], now=NOW)
        reg.discard(first, "teste", NOW)
        second = start(reg, NOW + timedelta(seconds=1))
        before = [reg.show(row["id"]) for row in reg.executions()]
    records = [
        json.loads((tmp_path / "s3" / key).read_text())
        for key in store.list_keys(EXPERIMENTS + "/")
    ]
    assert len(records) == 4
    path = tmp_path / "rebuilt" / "registry.sqlite"
    with rebuild(path, reversed(records), store) as rebuilt:
        assert [rebuilt.show(row["id"]) for row in rebuilt.executions()] == before
        assert rebuilt.count("A") == 1
        assert rebuilt.show(second)["status"] == "running"
