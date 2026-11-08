"""Comandos `trader baselines` e `trader experiments` (RF-256, RF-257, RF-276; doc 03 §5.3)."""

import json
from pathlib import Path

import pytest
from test_minutes import build_data

from trader.cli.main import main
from trader.experiments.registry import EXPERIMENTS, Registry
from trader.storage.objectstore import LocalDirectoryStore


def make_dataset(root: Path, out: Path, *extra: str, start: str = "2026-10-23") -> str:
    args = ["dataset", "--data-dir", str(root), "--track", "A", "--out-dir", str(out)]
    assert main([*args, "--start", start, "--end", "2026-10-24", *extra]) == 0
    manifests = sorted(out.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime)
    return manifests[-1].name.removesuffix(".manifest.json")


def baselines(root: Path, out: Path, registry: Path, dataset: str, *extra: str) -> int:
    return main(
        [
            "baselines",
            "--data-dir",
            str(root),
            "--dataset",
            dataset,
            "--horizon",
            "15",
            "--datasets-dir",
            str(out),
            "--registry",
            str(registry),
            *extra,
        ]
    )


def experiments(root: Path, registry: Path, *args: str) -> int:
    return main(["experiments", *args, "--data-dir", str(root), "--registry", str(registry)])


def test_baselines_command_registers_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_data(tmp_path / "data")
    out, registry = tmp_path / "datasets", tmp_path / "exp" / "registry.sqlite"
    dataset = make_dataset(root, out)
    capsys.readouterr()
    assert baselines(root, out, registry, dataset, "--b2-trades", "2", "--seed", "1") == 0
    printed = capsys.readouterr().out
    assert f"dataset {dataset} (trilha A) · H = 15 min · 2026-10-23..2026-10-24" in printed
    for line in ("B0 base", "B1 taxa_bnb", "B3 estresse", "B2 (2 operações, 1000 simulações"):
        assert line in printed, line
    with Registry(registry, LocalDirectoryStore(root)) as reg:
        (execution,) = reg.executions()
        shown = reg.show(execution["id"])
        assert (shown["kind"], shown["status"], shown["track"]) == ("baseline", "ok", "A")
        assert shown["spec"]["rungs"] == ["B0", "B1", "B3", "B2"]
        assert sorted(t["config"]["rung"] for t in shown["trials"]) == ["B0", "B1", "B2", "B3"]
        b3 = next(t for t in shown["trials"] if t["config"]["rung"] == "B3")
        assert set(b3["metrics"]["2026-10-23..2026-10-24"]) >= {"base", "estresse", "latencia_2s"}
        assert reg.count("A") == 0, "baselines não contam no IS-15"
    names = sorted(p.name for p in (root / EXPERIMENTS).iterdir())
    assert names == [f"{execution['id']}.result.json", f"{execution['id']}.start.json"]

    assert experiments(root, registry, "count") == 0
    assert "trilha A: 0 tentativas" in capsys.readouterr().out
    assert experiments(root, registry, "list") == 0
    assert execution["id"] in capsys.readouterr().out
    assert experiments(root, registry, "show", execution["id"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == execution["id"]
    assert experiments(root, registry, "discard", execution["id"]) == 2
    assert "motivo" in capsys.readouterr().err
    assert experiments(root, registry, "discard", execution["id"], "--reason", "teste") == 0
    assert experiments(root, registry, "show") == 2

    registry.unlink()
    assert experiments(root, registry, "rebuild") == 0
    assert "3 registros: 1 execuções" in capsys.readouterr().out
    with Registry(registry, LocalDirectoryStore(root)) as reg:
        assert reg.show(execution["id"])["discard_reason"] == "teste"


def test_dataset_comes_from_the_store_and_is_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_data(tmp_path / "data")
    out, registry = tmp_path / "datasets", tmp_path / "exp" / "registry.sqlite"
    dataset = make_dataset(root, out)
    for path in out.glob(f"{dataset}*"):
        path.unlink()
    assert baselines(root, out, registry, dataset) == 0, "baixa do armazenamento pelo id"
    assert (out / f"{dataset}.parquet").exists()
    with (out / f"{dataset}.parquet").open("ab") as file:
        file.write(b"x")
    capsys.readouterr()
    assert baselines(root, out, registry, dataset) == 2
    assert "SHA-256 diferente do manifesto" in capsys.readouterr().err
    with Registry(registry, LocalDirectoryStore(root)) as reg:
        statuses = [row["status"] for row in reg.executions()]
    assert statuses == ["ok", "error"], "o erro fica no registro"
    assert baselines(root, out, registry, "naoexiste") == 2


def test_final_evaluation_dataset_needs_the_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_data(tmp_path / "data")
    out, registry = tmp_path / "datasets", tmp_path / "exp" / "registry.sqlite"
    final = make_dataset(root, out, "--allow-holdout", start="2026-10-22")
    capsys.readouterr()
    assert baselines(root, out, registry, final) == 2
    assert "recusado" in capsys.readouterr().err
    with Registry(registry, LocalDirectoryStore(root)) as reg:
        assert reg.executions() == [], "recusado antes de registrar e de ler"
    assert baselines(root, out, registry, final, "--allow-holdout") == 0
    with Registry(registry, LocalDirectoryStore(root)) as reg:
        assert [row["final_evaluation"] for row in reg.executions()] == [True]
    dev = make_dataset(root, out)
    capsys.readouterr()
    assert baselines(root, out, registry, dev, "--allow-holdout") == 2
    assert "não é de avaliação final" in capsys.readouterr().err
    assert baselines(root, out, registry, dev, "--start", "2026-10-20") == 2
    assert "janela fora do dataset" in capsys.readouterr().err
