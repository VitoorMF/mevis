"""Comandos `trader walkforward` e `trader report` (RF-245 a RF-247, RF-281, RF-282; doc 03 §5.4).

Um dataset sintético de quatro meses (uma dobra) no formato do `trader dataset`, com uma feature de
candles que antecipa o retorno, para que o modelo opere.
"""

import io
import json
import shutil
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from trader.backtest.simulator import BASE
from trader.cli.main import main
from trader.experiments.registry import EXPERIMENTS, Registry
from trader.features.compute import FEATURES
from trader.models.walkforward import rung_features
from trader.pipeline.report import REPORTS
from trader.storage.objectstore import LocalDirectoryStore
from trader.storage.rawfiles import file_sha256

START, END = date(2025, 10, 23), date(2026, 2, 22)
DEV, OTHER, FINAL = "a" * 16, "b" * 16, "c" * 16


def write_dataset(out: Path, dataset_id: str, final: bool = False, seed: int = 0) -> str:
    rng = np.random.default_rng(seed)
    days = (END - START).days + 1
    n = days * 1440
    start_us = (START - date(1970, 1, 1)).days * 86_400_000_000
    price = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    columns: dict[str, pa.Array] = {
        "minute_ts_us": pa.array(start_us + np.arange(n, dtype=np.int64) * 60_000_000),
        "entry_price": pa.array(price),
        "entry_price_stress": pa.array(price * 1.0001),
        "close": pa.array(price),
        "high_prev_60": pa.array(price * 1.001),
    }
    lead = rung_features("B4")[0]
    targets = {}
    for h in (15, 60):
        valid = np.arange(n) < n - h - 1
        ahead = np.ones(n)
        ahead[: n - h] = price[h:] / price[: n - h]
        target = (ahead * BASE.keep / BASE.pay - 1 > 0).astype(np.int8)
        targets[h] = (ahead, valid)
        columns[f"eligible_h{h}"] = pa.array(valid)
        columns[f"target_h{h}"] = pa.array(target, mask=~valid)
    for name, _ in FEATURES:
        if name == lead:
            ahead, valid = targets[15]
            values = np.where(valid, np.log(ahead) * 1000, 0) + rng.normal(0, 0.5, n)
        else:
            values = rng.integers(0, 8, n).astype(np.float64)
        columns[name] = pa.array(values.astype(np.float32))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{dataset_id}.parquet"
    pq.write_table(pa.table(columns), path, compression="zstd")
    manifest = {
        "dataset_id": dataset_id,
        "final_evaluation": final,
        "parquet_sha256": file_sha256(path),
        "identity": {
            "track": "A",
            "start": START.isoformat(),
            "end": END.isoformat(),
            "catalog_id": "catalogo-teste",
            "label": {"horizons": [15, 60]},
            "final_evaluation": final,
        },
    }
    (out / f"{dataset_id}.manifest.json").write_text(json.dumps(manifest))
    return dataset_id


@dataclass(frozen=True)
class Flow:
    tmp: Path
    root: Path
    datasets: Path
    registry: Path
    reports: Path
    code: int
    out: str


def run(*args: str | Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def walkforward(flow: Flow, dataset: str, registry: Path | None = None) -> tuple[int, str, str]:
    return run(
        "walkforward",
        "--data-dir",
        flow.root,
        "--dataset",
        dataset,
        "--rungs",
        "B4",
        "--horizons",
        "15",
        "--models",
        "logistic",
        "--datasets-dir",
        flow.datasets,
        "--registry",
        registry or flow.registry,
    )


def report(flow: Flow, dataset: str, registry: Path | None = None) -> tuple[int, str, str]:
    return run(
        "report",
        "--data-dir",
        flow.root,
        "--dataset",
        dataset,
        "--datasets-dir",
        flow.datasets,
        "--registry",
        registry or flow.registry,
        "--reports-dir",
        flow.reports,
    )


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Flow:
    tmp = tmp_path_factory.mktemp("walkforward")
    datasets = tmp / "datasets"
    write_dataset(datasets, DEV)
    pending = Flow(
        tmp, tmp / "store", datasets, tmp / "exp" / "registry.sqlite", tmp / "rep", 0, ""
    )
    pending.root.mkdir()
    code, out, _ = walkforward(pending, DEV)
    return Flow(tmp, pending.root, datasets, pending.registry, pending.reports, code, out)


def only_execution(flow: Flow) -> dict[str, object]:
    with Registry(flow.registry, LocalDirectoryStore(flow.root)) as reg:
        (execution,) = reg.executions()
        return reg.show(str(execution["id"]))


def copy_registry(flow: Flow, name: str) -> Path:
    target = flow.tmp / name
    shutil.copytree(flow.registry.parent, target)
    return target / flow.registry.name


# ------------------------------------------------------------------ walk-forward (RF-245 a RF-247)


def test_walkforward_registers_outcome_trials_and_predictions(flow: Flow) -> None:
    assert flow.code == 0, flow.out
    shown = only_execution(flow)
    assert (shown["kind"], shown["status"], shown["track"]) == ("model", "ok", "A")
    assert shown["spec"]["rungs"] == ["B4"] and shown["spec"]["model"] == "logistic"  # type: ignore[index]
    assert shown["spec"]["windows"] == ["2026-01-23..2026-02-22"]  # type: ignore[index]
    eid = str(shown["id"])
    assert f"{eid} · B4 · H = 15 min · logistic" in flow.out
    assert "D1 2026-01-23..2026-02-22" in flow.out and "dobras juntas, base" in flow.out
    outcome = shown["outcome"]
    assert isinstance(outcome, dict)
    (fold,) = outcome["folds"]
    assert fold["windows"]["validation"] == "2025-12-23..2026-01-22"
    assert not fold["abstained"] and fold["test"]["base"]["trades"] > 0
    assert outcome["features"] == rung_features("B4")
    trials = shown["trials"]
    assert isinstance(trials, list) and len(trials) == 2 * 5
    config = trials[0]["config"]
    assert config["catalog_id"] == "catalogo-teste" and config["label"] == {"horizons": [15, 60]}
    assert set(trials[0]["metrics"]) == {"2025-12-23..2026-01-22", "validation"}
    sha = shown["artifacts"]["predictions.parquet"]  # type: ignore[index]
    remote = flow.root / EXPERIMENTS / eid / "predictions.parquet"
    local = flow.registry.parent / "artifacts" / eid / "predictions.parquet"
    assert file_sha256(remote) == sha == file_sha256(local)
    table = pq.read_table(local)
    assert table.column_names == ["minute_ts_us", "fold", "segment", "combo", "p"]
    with Registry(flow.registry, LocalDirectoryStore(flow.root)) as reg:
        assert reg.count("A") == 10


def test_rebuild_keeps_outcome_and_artifacts(flow: Flow) -> None:
    shown = only_execution(flow)
    rebuilt = flow.tmp / "rebuilt" / "registry.sqlite"
    code, out, _ = run("experiments", "rebuild", "--data-dir", flow.root, "--registry", rebuilt)
    assert code == 0, out
    with Registry(rebuilt, LocalDirectoryStore(flow.root)) as reg:
        again = reg.show(str(shown["id"]))
        assert again["outcome"] == shown["outcome"]
        assert again["artifacts"] == shown["artifacts"]
        assert reg.latest(DEV)[0]["id"] == shown["id"]
        # O arquivo de previsões vem do armazenamento quando falta no Mac.
        path = reg.artifact(str(shown["id"]), "predictions.parquet")
        assert path.parent.parent == rebuilt.parent / "artifacts" and path.exists()


def test_final_evaluation_dataset_is_refused(flow: Flow) -> None:
    write_dataset(flow.datasets, FINAL, final=True)
    registry = copy_registry(flow, "final")
    code, _, err = walkforward(flow, FINAL, registry)
    assert code == 2 and "recusado" in err and "avaliação final" in err
    with Registry(registry, LocalDirectoryStore(flow.root)) as reg:
        assert len(reg.executions()) == 1, "a recusa vem antes do registro"
    code, _, err = report(flow, FINAL, registry)
    assert code == 2 and "recusado" in err


# ------------------------------------------------------------------ relatório (RF-277 a RF-282)


def test_report_replays_predictions_and_writes_markdown(flow: Flow) -> None:
    code, out, err = report(flow, DEV)
    assert code == 0, err
    (path,) = flow.reports.glob("rep-*.md")
    text = path.read_text()
    assert (flow.root / REPORTS / path.name).read_text() == text
    assert f"relatório da trilha A · dataset {DEV} · 10 tentativas" in out
    for line in (
        f"- Dataset: `{DEV}` (2025-10-23 a 2026-02-22), catálogo `catalogo-teste`.",
        "Tentativas registradas na trilha A (IS-15): 10",
        "Janelas de teste: D1 2026-01-23..2026-02-22.",
        "## H = 15 min",
        "| B4 logística (ref.) |",
        "### Prévia dos critérios de G2",
        "**B4 logística (ref.)** (`",
    ):
        assert line in text, line
    for name in ("B0", "B1", "B3"):
        assert f"| {name} |" in text
    assert "Ablações" not in text, "as ablações comparam degraus do XGBoost"
    assert "## H = 60 min" not in text


def test_report_refuses_predictions_that_do_not_match(flow: Flow) -> None:
    eid = str(only_execution(flow)["id"])
    changed = copy_registry(flow, "changed")
    predictions = changed.parent / "artifacts" / eid / "predictions.parquet"
    table = pq.read_table(predictions)
    p = pc.subtract(pa.scalar(1.0), table["p"])
    pq.write_table(table.set_column(4, "p", p), predictions)
    code, _, err = report(flow, DEV, changed)
    assert code == 2 and "SHA-256" in err

    forged = copy_registry(flow, "forged")
    with sqlite3.connect(forged) as db:
        (text,) = db.execute(
            "SELECT outcome FROM outcomes WHERE execution_id = ?", (eid,)
        ).fetchone()
        outcome = json.loads(text)
        outcome["folds"][0]["threshold"] = 0.7 if outcome["folds"][0]["threshold"] != 0.7 else 0.5
        db.execute(
            "UPDATE outcomes SET outcome = ? WHERE execution_id = ?", (json.dumps(outcome), eid)
        )
    code, _, err = report(flow, DEV, forged)
    assert code == 2 and "não reproduzem" in err


def test_report_only_takes_executions_of_its_dataset(flow: Flow) -> None:
    shutil.copyfile(flow.datasets / f"{DEV}.parquet", flow.datasets / f"{OTHER}.parquet")
    manifest = json.loads((flow.datasets / f"{DEV}.manifest.json").read_text())
    manifest["dataset_id"] = OTHER
    (flow.datasets / f"{OTHER}.manifest.json").write_text(json.dumps(manifest))
    code, _, err = report(flow, OTHER)
    assert code == 2 and f"nenhuma execução de modelo terminada com o dataset {OTHER}" in err
