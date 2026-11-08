"""Execução dos baselines sobre um dataset (doc 03 §5.3; RF-231 a RF-234, RF-256, RF-257).

Lê o manifesto do dataset pelo id (do Mac ou do S3), recusa o de avaliação final sem a opção
(RF-256), registra a execução antes de ler o Parquet (RF-271), calcula B0, B1, B3 e, com o número
de operações dado, B2, em todos os cenários, e fecha o registro com as métricas (RF-272). Usa
`numpy`: só a CLI o importa, dentro do comando (RNF-029).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from trader.backtest.baselines import B2_SIMULATIONS, b0_signal, b1_trade, b2_totals, b3_signal
from trader.backtest.metrics import Metrics, metrics
from trader.backtest.simulator import Market, Run, Window, run_trades, simulate
from trader.core.holdout import HoldoutRefused
from trader.experiments.registry import Registry, Trial
from trader.pipeline.research import Fetcher, dataset_file, read_manifest

MODELS = {
    "B0": "nenhuma_operacao",
    "B1": "comprar_e_segurar",
    "B2": "entradas_aleatorias",
    "B3": "rompimento_60",
}


@dataclass(frozen=True)
class BaselineRequest:
    dataset_id: str
    horizon: int
    first_day: date | None = None
    last_day: date | None = None
    b2_trades: int | None = None
    seed: int = 0
    final_evaluation: bool = False


@dataclass
class BaselineReport:
    execution_id: str
    track: str
    window: Window
    metrics: dict[str, dict[str, Metrics]]
    """Por baseline e cenário."""
    b2: np.ndarray | None
    seconds: dict[str, float] = field(default_factory=dict)
    """Tempo de cada parte, para a RNF-031."""

    def b2_summary(self) -> dict[str, float] | None:
        """Média e percentis 5, 50 e 95 do retorno líquido total de B2."""
        if self.b2 is None:
            return None
        p05, p50, p95 = np.percentile(self.b2, [5, 50, 95])
        return {
            "simulations": len(self.b2),
            "mean": float(np.mean(self.b2)),
            "p05": float(p05),
            "p50": float(p50),
            "p95": float(p95),
        }


def run_baselines(
    request: BaselineRequest,
    *,
    datasets_dir: Path,
    fetcher: Fetcher,
    registry: Registry,
    code: str,
    who: str,
    clock: Callable[[], datetime],
) -> BaselineReport:
    manifest = read_manifest(request.dataset_id, datasets_dir, fetcher)
    identity = manifest["identity"]
    final = bool(manifest["final_evaluation"])
    if final and not request.final_evaluation:
        raise HoldoutRefused(
            f"dataset {request.dataset_id} é de avaliação final: só com a opção (RF-256)"
        )
    if request.final_evaluation and not final:
        raise ValueError(f"dataset {request.dataset_id} não é de avaliação final")
    if request.horizon not in identity["label"]["horizons"]:
        raise ValueError(f"horizonte {request.horizon} não está no dataset")
    first = request.first_day or date.fromisoformat(identity["start"])
    last = request.last_day or date.fromisoformat(identity["end"])
    if first < date.fromisoformat(identity["start"]) or last > date.fromisoformat(identity["end"]):
        raise ValueError(f"janela fora do dataset: {first} a {last}")
    rungs = ["B0", "B1", "B3"] + (["B2"] if request.b2_trades is not None else [])
    window_label = f"{first.isoformat()}..{last.isoformat()}"
    execution_id = registry.start(
        kind="baseline",
        track=identity["track"],
        dataset_id=request.dataset_id,
        final_evaluation=final,
        code_version=code,
        who=who,
        spec={
            "rungs": rungs,
            "horizons": [request.horizon],
            "model": None,
            "grid": None,
            "seed": request.seed if request.b2_trades is not None else None,
            "windows": [window_label],
            "b2_trades": request.b2_trades,
        },
        now=clock(),
    )
    try:
        report = _run(request, manifest, datasets_dir, fetcher, execution_id, first, last)
    except Exception as exc:
        registry.finish(execution_id, trials=[], now=clock(), error=f"{type(exc).__name__}: {exc}")
        raise
    registry.finish(execution_id, trials=_trials(request, identity, report), now=clock())
    return report


def _run(
    request: BaselineRequest,
    manifest: dict[str, Any],
    datasets_dir: Path,
    fetcher: Fetcher,
    execution_id: str,
    first: date,
    last: date,
) -> BaselineReport:
    path = dataset_file(request.dataset_id, manifest, datasets_dir, fetcher)
    market = Market.from_table(pq.read_table(path))
    window = market.window(first, last)
    h = request.horizon
    seconds: dict[str, float] = {}
    runs: dict[str, Run] = {}
    began = time.monotonic()
    runs["B0"] = simulate(market, b0_signal(market), h, window)
    decisions, exits = b1_trade(market, window)
    runs["B1"] = run_trades(market, decisions, exits, window, h)
    seconds["B0 e B1"] = time.monotonic() - began
    began = time.monotonic()
    runs["B3"] = simulate(market, b3_signal(market), h, window)
    seconds["B3"] = time.monotonic() - began
    b2 = None
    if request.b2_trades is not None:
        began = time.monotonic()
        b2 = b2_totals(market, h, [(window, request.b2_trades)], seed=request.seed)
        seconds["B2"] = time.monotonic() - began
    results = {
        rung: {name: metrics(result) for name, result in run.paths.items()}
        for rung, run in runs.items()
    }
    return BaselineReport(execution_id, manifest["identity"]["track"], window, results, b2, seconds)


def _trials(
    request: BaselineRequest, identity: dict[str, Any], report: BaselineReport
) -> list[Trial]:
    label = report.window.label

    def config(rung: str, hyperparameters: dict[str, Any], seed: int | None) -> dict[str, Any]:
        return {
            "rung": rung,
            "horizon": request.horizon,
            "model": MODELS[rung],
            "hyperparameters": hyperparameters,
            "threshold": None,
            "seed": seed,
            "catalog_id": identity["catalog_id"],
            "label": identity["label"],
        }

    trials = []
    for rung, by_scenario in report.metrics.items():
        hyperparameters = {"window": 60} if rung == "B3" else {}
        values = {label: {name: m.to_dict() for name, m in by_scenario.items()}}
        trials.append(Trial(config(rung, hyperparameters, None), values))
    summary = report.b2_summary()
    if summary is not None:
        b2_parameters = {"trades": request.b2_trades, "simulations": B2_SIMULATIONS}
        b2_values = {label: {"base": summary}}
        trials.append(Trial(config("B2", b2_parameters, request.seed), b2_values))
    return trials
