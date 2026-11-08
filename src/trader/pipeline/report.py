"""Relatório de desenvolvimento (doc 04 §10.1; doc 03 §5.4; RF-277 a RF-282).

Junta a execução de modelo mais recente de cada degrau, horizonte e modelo do dataset, refaz a
simulação do teste a partir das previsões guardadas (sem treinar de novo) e conferindo que ela bate
com o registro, calcula os baselines nas mesmas janelas, B2 com as operações do modelo em cada
dobra, o bootstrap do IS-14, o DSR informativo e as ablações, e grava um Markdown. O
desenvolvimento não aprova nada: G2 se decide no holdout, menos o IS-18.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from trader.backtest.baselines import b0_signal, b1_trade, b2_totals, b3_signal
from trader.backtest.metrics import ANNUALIZATION, Metrics, daily_returns, metrics
from trader.backtest.simulator import (
    DAY_US,
    REPORTED,
    Market,
    Run,
    chain,
    run_trades,
    simulate,
)
from trader.backtest.simulator import (
    Path as TradePath,
)
from trader.core.holdout import HoldoutRefused
from trader.experiments.registry import Registry
from trader.models.folds import Fold, folds, months
from trader.models.walkforward import TEST, replay
from trader.pipeline.research import Fetcher, dataset_file, read_manifest
from trader.pipeline.walkforward import MARKET_COLUMNS, PREDICTIONS
from trader.stats.bootstrap import Days, Interval, expectancy_interval, paired
from trader.stats.dsr import Deflated, deflated_sharpe, trial_variance
from trader.storage.objectstore import ObjectStore
from trader.storage.rawfiles import file_sha256

REPORTS = "derived/reports"
MAX_DRAWDOWN = 0.15
"""Limite do IS-19 até o doc 05 definir outro (doc 04 §10.2)."""
MIN_TRADES_G2 = 200
POSITIVE_FOLDS = 2 / 3
ABLATIONS = (("B5", "B4", "H-02"), ("B6", "B5", "H-03, parte 1"))
MAIN_MODEL = "xgboost"
MODEL_NAMES = {"xgboost": "XGBoost", "logistic": "logística (ref.)"}


@dataclass(frozen=True)
class Strategy:
    name: str
    rung: str
    horizon: int
    model: str
    execution_id: str | None
    runs: list[Run]
    """Um por dobra de teste."""
    outcome: dict[str, Any] | None = None

    def path(self, scenario: str = "base") -> TradePath:
        return chain([run.paths[scenario] for run in self.runs])

    def metrics(self, scenario: str = "base") -> Metrics:
        return metrics(self.path(scenario))

    def fold_metrics(self, scenario: str = "base") -> list[Metrics]:
        return [metrics(run.paths[scenario]) for run in self.runs]


@dataclass(frozen=True)
class Report:
    report_id: str
    path: Path
    key: str
    markdown: str
    summary: str


def build_report(
    dataset_id: str,
    *,
    datasets_dir: Path,
    fetcher: Fetcher,
    store: ObjectStore,
    registry: Registry,
    reports_dir: Path,
    seed: int,
    code: str,
    clock: Callable[[], datetime],
) -> Report:
    manifest = read_manifest(dataset_id, datasets_dir, fetcher)
    identity = manifest["identity"]
    if manifest["final_evaluation"]:
        raise HoldoutRefused(
            f"dataset {dataset_id} é de avaliação final: o relatório da etapa 6 é do"
            " desenvolvimento"
        )
    executions = registry.latest(dataset_id, "model")
    if not executions:
        raise ValueError(f"nenhuma execução de modelo terminada com o dataset {dataset_id}")
    horizons = sorted({int(e["outcome"]["horizon"]) for e in executions})
    path = dataset_file(dataset_id, manifest, datasets_dir, fetcher)
    columns = [*MARKET_COLUMNS, *(f"eligible_h{h}" for h in horizons)]
    market = Market.from_table(pq.read_table(path, columns=columns))
    periods = months(date.fromisoformat(identity["start"]), date.fromisoformat(identity["end"]))
    test_folds = folds(market, periods)

    strategies = [_strategy(market, test_folds, registry, e) for e in executions]
    track = identity["track"]
    trials = registry.count(track)
    variance = trial_variance(
        [
            None if s is None else s / ANNUALIZATION
            for s in (
                m.get("validation", {}).get("base", {}).get("sharpe")
                for m in registry.trial_metrics(track).values()
            )
        ]
    )
    now = clock().astimezone(UTC)
    lines = [
        f"# Relatório de desenvolvimento — trilha {track}",
        "",
        f"- Dataset: `{dataset_id}` ({identity['start']} a {identity['end']}), catálogo"
        f" `{identity['catalog_id']}`.",
        f"- Gerado em {now.isoformat(timespec='seconds')} com o código `{code}`; semente {seed}.",
        f"- Tentativas registradas na trilha {track} (IS-15): {trials}; variância dos Sharpe"
        f" diários na validação: {variance:.3g}.",
        "- Janelas de teste: " + ", ".join(f"{f.name} {f.test.label}" for f in test_folds) + ".",
        "- Desenvolvimento: G2 se decide no holdout (doc 04 §10.2), menos o IS-18. Os critérios"
        " abaixo são uma prévia.",
        "",
    ]
    summary = [f"relatório da trilha {track} · dataset {dataset_id} · {trials} tentativas"]
    for h in horizons:
        mine = sorted(
            (s for s in strategies if s.horizon == h), key=lambda s: (s.rung, s.model != "logistic")
        )
        baselines = _baselines(market, test_folds, h)
        section, short = _horizon(market, test_folds, h, mine, baselines, trials, variance, seed)
        lines += section
        summary += short
    markdown = "\n".join(lines) + "\n"

    report_id = "rep-" + now.strftime("%Y%m%dT%H%M%S.%fZ")
    local = reports_dir / f"{report_id}.md"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(markdown)
    key = f"{REPORTS}/{report_id}.md"
    store.put(local, key, file_sha256(local))
    return Report(report_id, local, key, markdown, "\n".join(summary))


def _strategy(
    market: Market, test_folds: Sequence[Fold], registry: Registry, execution: dict[str, Any]
) -> Strategy:
    outcome = execution["outcome"]
    h = int(outcome["horizon"])
    table = pq.read_table(registry.artifact(execution["id"], PREDICTIONS))
    segment = table["segment"].to_numpy()
    fold_ids = table["fold"].to_numpy()
    minute = table["minute_ts_us"].to_numpy()
    p = table["p"].to_numpy()
    runs = []
    if len(outcome["folds"]) != len(test_folds):
        raise ValueError(f"{execution['id']}: dobras diferentes das do dataset")
    for k, (fold, recorded) in enumerate(zip(test_folds, outcome["folds"], strict=True)):
        if recorded["windows"]["test"] != fold.test.label:
            raise ValueError(f"{execution['id']}: janela de teste diferente em {fold.name}")
        rows = (segment == TEST) & (fold_ids == k)
        idx = np.searchsorted(market.minute_ts_us, minute[rows])
        run = replay(market, h, fold.test, idx, p[rows], recorded["threshold"])
        if metrics(run.paths["base"]).to_dict() != recorded["test"]["base"]:
            raise ValueError(f"{execution['id']}: as previsões não reproduzem {fold.name}")
        runs.append(run)
    name = f"{outcome['rung']} {MODEL_NAMES.get(outcome['model'], outcome['model'])}"
    return Strategy(name, outcome["rung"], h, outcome["model"], execution["id"], runs, outcome)


def _baselines(market: Market, test_folds: Sequence[Fold], h: int) -> dict[str, Strategy]:
    b0, b1, b3 = [], [], []
    for fold in test_folds:
        b0.append(simulate(market, b0_signal(market), h, fold.test))
        decisions, exits = b1_trade(market, fold.test)
        b1.append(run_trades(market, decisions, exits, fold.test, h))
        b3.append(simulate(market, b3_signal(market), h, fold.test))
    return {
        "B0": Strategy("B0", "B0", h, "nenhuma_operacao", None, b0),
        "B1": Strategy("B1", "B1", h, "comprar_e_segurar", None, b1),
        "B3": Strategy("B3", "B3", h, "rompimento_60", None, b3),
    }


def days_of(market: Market, strategy: Strategy, scenario: str = "base") -> Days:
    """Retornos diários e operações pelo dia da saída, nas dobras juntas (RF-277)."""
    day_numbers = np.concatenate(
        [
            market.minute_ts_us[run.window.start : run.window.stop][run.paths[scenario].day_last]
            // DAY_US
            for run in strategy.runs
        ]
    )
    exits = np.concatenate([run.exits for run in strategy.runs]).astype(np.int64)
    trade_days = np.searchsorted(day_numbers, market.minute_ts_us[exits + 1] // DAY_US)
    returns = np.concatenate([run.paths[scenario].returns for run in strategy.runs])
    return Days(daily_returns(strategy.path(scenario)), returns, trade_days)


def _horizon(
    market: Market,
    test_folds: Sequence[Fold],
    h: int,
    strategies: Sequence[Strategy],
    baselines: dict[str, Strategy],
    trials: int,
    variance: float,
    seed: int,
) -> tuple[list[str], list[str]]:
    rows = [baselines["B0"], baselines["B1"], baselines["B3"], *strategies]
    out = [f"## H = {h} min", "", "### Dobras de teste juntas, cenário base", ""]
    out += [
        "| Estratégia | Operações | Expect. (pb) | Retorno | Sharpe | Drawdown | Acerto | Exposição"
        " | Abstenções |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in rows:
        m = s.metrics()
        abstained = "—"
        if s.outcome is not None:
            abstained = f"{sum(f['abstained'] for f in s.outcome['folds'])} de {len(s.runs)}"
        out.append(
            f"| {s.name} | {m.trades} | {m.expectancy_bps:.1f} | {_pct(m.total_return)} |"
            f" {_num(m.sharpe)} | {_pct(m.max_drawdown)} | {_pct(m.hit_rate)} |"
            f" {_pct(m.exposure)} | {abstained} |"
        )
    out += ["", "### Retorno nas dobras juntas, por cenário", ""]
    out += ["| Estratégia | " + " | ".join(REPORTED) + " |", "|---" * (len(REPORTED) + 1) + "|"]
    for s in rows:
        values = " | ".join(_pct(s.metrics(name).total_return) for name in REPORTED)
        out.append(f"| {s.name} | {values} |")

    out += ["", "### Prévia dos critérios de G2", ""]
    out += [
        "| Estratégia | IS-14: IC 95% da expectativa (pb) | IS-15: DSR (informativo) | IS-16:"
        " Sharpe > B1 e B3 | IS-16: acima do p95 de B2 | IS-16: acima de B0 | IS-17: operações"
        " | IS-18: dobras positivas | IS-19: estresse | IS-19: drawdown ≤ 15%, base / estresse |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    short = []
    b1, b3 = baselines["B1"].metrics(), baselines["B3"].metrics()
    for s in strategies:
        m, stress = s.metrics(), s.metrics("estresse")
        days = days_of(market, s)
        ci = expectancy_interval(days, seed)
        dsr = deflated_sharpe(days.daily, max(trials, 1), variance)
        plan = [
            (fold.test, len(run.paths["base"].returns))
            for fold, run in zip(test_folds, s.runs, strict=True)
        ]
        b2 = b2_totals(market, h, plan, seed)
        p95 = float(np.percentile(b2, 95))
        beats = _beats(m.sharpe, b1.sharpe) and _beats(m.sharpe, b3.sharpe)
        positive = sum(fm.total_return > 0 for fm in s.fold_metrics())
        needed = math.ceil(POSITIVE_FOLDS * len(s.runs))
        out.append(
            f"| {s.name} | {_ci(ci)} | {_dsr(dsr)} | {_yes(beats)} |"
            f" {_yes(m.total_return > p95)} ({_pct(p95)}) | {_yes(m.total_return > 0)} |"
            f" {m.trades} (mín. {MIN_TRADES_G2} no holdout) | {positive} de {len(s.runs)}"
            f" ({_yes(positive >= needed)}) | {_yes(stress.total_return > 0)} |"
            f" {_pct(m.max_drawdown)} ({_yes(m.max_drawdown <= MAX_DRAWDOWN)}) /"
            f" {_pct(stress.max_drawdown)} ({_yes(stress.max_drawdown <= MAX_DRAWDOWN)}) |"
        )
        if s.model == MAIN_MODEL:
            short.append(
                f"H = {h} · {s.name}: {m.trades} operações · {m.expectancy_bps:.1f} pb · retorno"
                f" {_pct(m.total_return)} · Sharpe {_num(m.sharpe)} · dobras positivas"
                f" {positive} de {len(s.runs)}"
            )

    main = {s.rung: s for s in strategies if s.model == MAIN_MODEL}
    pairs = [(a, b, label) for a, b, label in ABLATIONS if a in main and b in main]
    if pairs:
        out += ["", "### Ablações (XGBoost, bootstrap pareado nos mesmos dias)", ""]
        out += [
            "| Comparação | Δ expectativa (pb), IC 95% | Δ Sharpe, IC 95% | Percentil 2,5 > 0"
            " (expectativa / Sharpe) |",
            "|---|---|---|---|",
        ]
        for a, b, label in pairs:
            de, ds = paired(days_of(market, main[a]), days_of(market, main[b]), seed)
            out.append(
                f"| {a} - {b} ({label}) | {_ci(de)} | {_ci(ds, scale=1)} |"
                f" {_yes(_above_zero(de))} / {_yes(_above_zero(ds))} |"
            )

    out += ["", "### Dobras", ""]
    for s in strategies:
        assert s.outcome is not None
        out += [f"**{s.name}** (`{s.execution_id}`)", ""]
        out += [
            "| Dobra | Teste | Hiperparâmetros | τ | Operações | Retorno base | Retorno estresse"
            " | AUC | Log loss |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for f, run in zip(s.outcome["folds"], s.runs, strict=True):
            params = ", ".join(f"{k} = {v}" for k, v in f["hyperparameters"].items())
            tau = "abstenção" if f["abstained"] else f"{f['threshold']:.2f}"
            base, stress = metrics(run.paths["base"]), metrics(run.paths["estresse"])
            out.append(
                f"| {f['fold']} | {f['windows']['test']} | {params} | {tau} | {base.trades} |"
                f" {_pct(base.total_return)} | {_pct(stress.total_return)} |"
                f" {_num(f['test_auc'], 3)} | {f['test_log_loss']:.4f} |"
            )
        out.append("")
        importance = _importance(s.outcome)
        if importance:
            top = ", ".join(f"`{name}` {value:.3g}" for name, value in importance[:10])
            out += [f"Importância média nas dobras (10 maiores): {top}.", ""]
    return out, short


def _importance(outcome: dict[str, Any]) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for f in outcome["folds"]:
        for name, value in f["importance"].items():
            totals[name] = totals.get(name, 0.0) + value / len(outcome["folds"])
    return sorted(totals.items(), key=lambda item: -item[1])


def _above_zero(ci: Interval) -> bool:
    return ci.low is not None and ci.low > 0


def _beats(a: float | None, b: float | None) -> bool:
    if a is None:
        return False
    return b is None or a > b


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _yes(flag: bool) -> str:
    return "sim" if flag else "não"


def _ci(ci: Interval, scale: float = 1e4) -> str:
    if ci.low is None or ci.high is None or not math.isfinite(ci.estimate):
        return "—"
    empty = f"; {ci.empty} amostras vazias" if ci.empty else ""
    return f"{ci.estimate * scale:.2f} [{ci.low * scale:.2f}; {ci.high * scale:.2f}]{empty}"


def _dsr(d: Deflated) -> str:
    return "—" if d.dsr is None else f"{d.dsr:.3f} (SR0 {d.expected_max:.3f})"
