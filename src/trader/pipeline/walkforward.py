"""Execução do walk-forward sobre um dataset (doc 03 §5.4; RF-245 a RF-247).

Cada combinação de degrau, horizonte e modelo é uma execução: registrada antes de ler o Parquet
(RF-271), roda as dobras, guarda as previsões e fecha o registro com as tentativas e o resultado do
procedimento. Uma combinação que falha fica com o erro no registro e não impede as outras. Usa
`numpy`, `scikit-learn` e `xgboost`: só a CLI importa este módulo, dentro do comando (RNF-029).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.backtest.simulator import Market
from trader.core.holdout import HoldoutRefused
from trader.experiments.registry import Registry, Trial
from trader.models.estimators import GRIDS, MODELS, SEED, TAUS
from trader.models.folds import INITIAL_MONTHS, folds, months
from trader.models.walkforward import RUNGS, Task, WalkForward, rung_features, walk_forward
from trader.pipeline.research import Fetcher, dataset_file, read_manifest

PREDICTIONS = "predictions.parquet"
MARKET_COLUMNS = ("minute_ts_us", "entry_price", "entry_price_stress", "close", "high_prev_60")


@dataclass(frozen=True)
class WalkForwardRequest:
    dataset_id: str
    rungs: tuple[str, ...] = tuple(RUNGS)
    horizons: tuple[int, ...] | None = None
    """Sem horizontes, os do dataset (manifesto; doc 03 §5.5)."""
    models: tuple[str, ...] = MODELS
    seed: int = SEED


@dataclass(frozen=True)
class TaskResult:
    task: Task
    execution_id: str
    result: WalkForward | None
    error: str | None
    seconds: float


def run_walkforward(
    request: WalkForwardRequest,
    *,
    datasets_dir: Path,
    fetcher: Fetcher,
    registry: Registry,
    code: str,
    who: str,
    clock: Callable[[], datetime],
    on_result: Callable[[TaskResult], None] | None = None,
) -> list[TaskResult]:
    manifest = read_manifest(request.dataset_id, datasets_dir, fetcher)
    identity = manifest["identity"]
    if manifest["final_evaluation"]:
        raise HoldoutRefused(
            f"dataset {request.dataset_id} é de avaliação final: o walk-forward da etapa 6 só usa"
            " o desenvolvimento (RF-246)"
        )
    for rung in request.rungs:
        if rung not in RUNGS:
            raise ValueError(f"degrau desconhecido: {rung}")
    horizons = request.horizons or tuple(int(h) for h in identity["label"]["horizons"])
    for h in horizons:
        if h not in identity["label"]["horizons"]:
            raise ValueError(f"horizonte {h} não está no dataset")
    for model in request.models:
        if model not in MODELS:
            raise ValueError(f"modelo desconhecido: {model}")
    periods = months(date.fromisoformat(identity["start"]), date.fromisoformat(identity["end"]))
    tests = [f"{a.isoformat()}..{b.isoformat()}" for a, b in periods[INITIAL_MONTHS:]]

    out: list[TaskResult] = []
    for rung, h, model in product(request.rungs, horizons, request.models):
        task = Task(rung, h, model, request.seed)
        spec = {
            "rungs": [rung],
            "horizons": [h],
            "model": model,
            "grid": {"hyperparameters": [dict(g) for g in GRIDS[model]], "thresholds": list(TAUS)},
            "seed": request.seed,
            "windows": tests,
        }
        execution_id = registry.start(
            kind="model",
            track=identity["track"],
            dataset_id=request.dataset_id,
            final_evaluation=False,
            code_version=code,
            who=who,
            spec=spec,
            now=clock(),
        )
        began = time.monotonic()
        try:
            path = dataset_file(request.dataset_id, manifest, datasets_dir, fetcher)
            result, minute_ts_us = _run(path, task, periods)
            local = registry.artifacts / execution_id / PREDICTIONS
            write_predictions(local, result, minute_ts_us)
            sha = registry.put_artifact(execution_id, local)
            registry.finish(
                execution_id,
                trials=_trials(result, identity),
                now=clock(),
                outcome=result.outcome(),
                artifacts={PREDICTIONS: sha},
            )
            done = TaskResult(task, execution_id, result, None, time.monotonic() - began)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            registry.finish(execution_id, trials=[], now=clock(), error=error)
            done = TaskResult(task, execution_id, None, error, time.monotonic() - began)
        out.append(done)
        if on_result is not None:
            on_result(done)
    return out


def load(path: Path, features: Sequence[str], horizons: Sequence[int]) -> pa.Table:
    """Só as colunas do degrau, do alvo e da simulação."""
    columns = [*MARKET_COLUMNS, *features]
    for h in horizons:
        columns += [f"eligible_h{h}", f"target_h{h}"]
    return pq.read_table(path, columns=columns)


def matrix(table: pa.Table, names: Sequence[str]) -> np.ndarray:
    """Colunas em FLOAT64, uma por nome, com NaN onde são nulas."""
    if not names:
        return np.zeros((table.num_rows, 0))
    return np.column_stack(
        [
            np.asarray(
                pc.cast(table[name], pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64
            )
            for name in names
        ]
    )


def write_predictions(local: Path, result: WalkForward, minute_ts_us: np.ndarray) -> None:
    """Previsões de validação e de teste (minuto, dobra, trecho, combinação, p) (RF-245)."""
    p = result.predictions
    table = pa.table(
        {
            "minute_ts_us": pa.array(minute_ts_us[p.minute], pa.int64()),
            "fold": pa.array(p.fold, pa.int8()),
            "segment": pa.array(p.segment, pa.int8()),
            "combo": pa.array(p.combo, pa.int8()),
            "p": pa.array(p.p, pa.float64()),
        }
    )
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(local)


def _run(
    path: Path, task: Task, periods: Sequence[tuple[date, date]]
) -> tuple[WalkForward, np.ndarray]:
    features = rung_features(task.rung)
    table = load(path, features, [task.horizon])
    market = Market.from_table(table)
    x = matrix(table, features)
    target = matrix(table, [f"target_h{task.horizon}"])[:, 0]
    result = walk_forward(market, x, target, task, folds(market, periods), features)
    return result, market.minute_ts_us


def _trials(result: WalkForward, identity: dict[str, Any]) -> list[Trial]:
    task = result.task
    return [
        Trial(
            {
                "rung": task.rung,
                "horizon": task.horizon,
                "model": task.model,
                "hyperparameters": dict(trial.hyperparameters),
                "threshold": trial.threshold,
                "seed": task.seed,
                "catalog_id": identity["catalog_id"],
                "label": identity["label"],
            },
            trial.metrics,
        )
        for trial in result.trials
    ]
