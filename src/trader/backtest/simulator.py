"""Simulador de operações com custos (doc 04 §4.4, §9 e §10.1; RF-251 a RF-254). Sem E/S.

Uma estratégia é um sinal por minuto. O simulador opera só nos pontos elegíveis do horizonte cuja
saída cai na janela: com sinal no minuto t e sem posição, entra no preço de execução do minuto
t + 1 e sai no do minuto t + 1 + H; o próximo sinal considerado é o do minuto t + H + 1. As
operações não dependem do cenário, só os preços e os custos (DEC-38).

Os preços vêm do dataset: `entry_price[t]` é o preço de execução do minuto t + 1, então o preço de
saída de uma operação decidida em t é `entry_price[t + H]` (doc 04 §11). Uma operação é guardada
como o par (decisão, índice do preço de saída); B1 usa o mesmo par com uma duração qualquer.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from trader.core.timeunits import MINUTE_US
from trader.datasets.labels import FEE, SLIPPAGE

DAY_US = 1440 * MINUTE_US


@dataclass(frozen=True)
class Scenario:
    name: str
    fee: float
    """Taxa por lado."""
    slippage: float
    """Spread e deslizamento por lado, contra a operação."""
    stress_latency: bool = False
    """Latência de 2 s: preço de execução a 4 s do início do minuto."""

    @property
    def keep(self) -> float:
        """Fração do valor de venda que sobra: deslizamento na venda e taxa nos dois lados."""
        return (1 - self.slippage) * (1 - self.fee) ** 2

    @property
    def pay(self) -> float:
        """Multiplicador do preço de compra: deslizamento contra a operação."""
        return 1 + self.slippage


BASE = Scenario("base", FEE, SLIPPAGE)
SCENARIOS = (
    BASE,
    Scenario("taxa_bnb", 0.00075, SLIPPAGE),
    Scenario("taxa_estresse", 0.0015, SLIPPAGE),
    Scenario("sem_deslizamento", FEE, 0.0),
    Scenario("deslizamento_alto", FEE, 0.0003),
    Scenario("latencia_2s", FEE, SLIPPAGE, stress_latency=True),
    Scenario("estresse", 0.0015, 0.0003, stress_latency=True),
)
"""Os sete cenários de doc 04 §9 (DEC-38); `estresse` é o do IS-19."""
REPORTED = ("base", "taxa_bnb", "taxa_estresse", "estresse")
"""Cenários que aparecem sempre no relatório; os outros são sensibilidade."""


@dataclass(frozen=True)
class Window:
    """Dias UTC inteiros do dataset: índices `[start, stop)` dos minutos (doc 04 §10.1)."""

    start: int
    stop: int
    first_day: date
    last_day: date

    @property
    def label(self) -> str:
        return f"{self.first_day.isoformat()}..{self.last_day.isoformat()}"


@dataclass(frozen=True)
class Market:
    """As colunas do dataset que o simulador e os baselines usam, em FLOAT64 (NaN onde não há)."""

    minute_ts_us: np.ndarray
    entry_price: np.ndarray
    entry_price_stress: np.ndarray
    close: np.ndarray
    high_prev_60: np.ndarray
    eligible: Mapping[int, np.ndarray]
    """Pontos elegíveis por horizonte (RF-225)."""

    @classmethod
    def from_table(cls, table: pa.Table) -> "Market":
        ts = table["minute_ts_us"].to_numpy()
        if len(ts) == 0 or (np.diff(ts) != MINUTE_US).any() or ts[0] % DAY_US:
            raise ValueError("o dataset precisa de minutos contíguos desde o início de um dia")
        horizons = sorted(
            int(name.removeprefix("eligible_h"))
            for name in table.column_names
            if name.startswith("eligible_h")
        )
        return cls(
            minute_ts_us=ts,
            entry_price=_floats(table, "entry_price"),
            entry_price_stress=_floats(table, "entry_price_stress"),
            close=_floats(table, "close"),
            high_prev_60=_floats(table, "high_prev_60"),
            eligible={
                h: table[f"eligible_h{h}"].fill_null(False).to_numpy(zero_copy_only=False)
                for h in horizons
            },
        )

    def window(self, first_day: date, last_day: date) -> Window:
        """Janela de dias inteiros, que precisa estar toda dentro do dataset."""
        lo = _day_start_us(first_day)
        hi = _day_start_us(last_day + timedelta(days=1))
        start = int(np.searchsorted(self.minute_ts_us, lo))
        stop = int(np.searchsorted(self.minute_ts_us, hi))
        if (
            last_day < first_day
            or start >= len(self.minute_ts_us)
            or self.minute_ts_us[start] != lo
            or self.minute_ts_us[stop - 1] != hi - MINUTE_US
        ):
            raise ValueError(f"janela fora do dataset: {first_day} a {last_day}")
        return Window(start, stop, first_day, last_day)

    def full_window(self) -> Window:
        first = _day(int(self.minute_ts_us[0]))
        last = _day(int(self.minute_ts_us[-1]))
        return self.window(first, last)


@dataclass(frozen=True)
class Path:
    """Resultado de uma estratégia numa janela e num cenário."""

    returns: np.ndarray
    """Retorno líquido de cada operação, em ordem."""
    held_minutes: int
    """Minutos com posição aberta (para a exposição)."""
    equity: np.ndarray
    """Capital no fechamento de cada minuto da janela, marcado pelo valor de liquidação (RF-254)."""
    day_last: np.ndarray
    """Índices, em `equity`, do último minuto de cada dia UTC."""
    fallbacks: int = 0
    """Preços de estresse ausentes trocados pelo base (RF-253)."""


@dataclass(frozen=True)
class Run:
    """Uma estratégia numa janela e num horizonte, em todos os cenários pedidos."""

    window: Window
    horizon: int
    decisions: np.ndarray
    exits: np.ndarray
    """Índice do preço de saída: a saída acontece no minuto `exits + 1`."""
    paths: Mapping[str, Path]


def select_trades(
    signal: np.ndarray, eligible: np.ndarray, horizon: int, window: Window
) -> np.ndarray:
    """Decisões da regra sem sobreposição (RF-251): o laço percorre só as operações."""
    last = window.stop - 2 - horizon  # último t com a saída, no minuto t + 1 + H, na janela
    if last < window.start:
        return np.zeros(0, dtype=np.int64)
    span = slice(window.start, last + 1)
    candidates = np.flatnonzero(signal[span] & eligible[span]) + window.start
    chosen: list[int] = []
    i = 0
    while i < len(candidates):
        t = int(candidates[i])
        chosen.append(t)
        i = int(np.searchsorted(candidates, t + horizon + 1))
    return np.asarray(chosen, dtype=np.int64)


def trade_returns(
    market: Market, decisions: np.ndarray, exits: np.ndarray, scenario: Scenario
) -> tuple[np.ndarray, np.ndarray, int]:
    """Retorno líquido de cada operação (RF-252), o preço de entrada usado e os preços trocados.

    É a fórmula do alvo (RF-223) com a taxa, o deslizamento e a latência do cenário."""
    entry = market.entry_price[decisions]
    exit_price = market.entry_price[exits]
    fallbacks = 0
    if scenario.stress_latency:
        stress_entry = market.entry_price_stress[decisions]
        stress_exit = market.entry_price_stress[exits]
        missing_entry, missing_exit = np.isnan(stress_entry), np.isnan(stress_exit)
        fallbacks = int(missing_entry.sum() + missing_exit.sum())
        entry = np.where(missing_entry, entry, stress_entry)
        exit_price = np.where(missing_exit, exit_price, stress_exit)
    if not (np.isfinite(entry).all() and np.isfinite(exit_price).all()):
        raise ValueError("operação sem preço de execução")
    returns = exit_price * scenario.keep / (entry * scenario.pay) - 1
    return returns, entry, fallbacks


def run_trades(
    market: Market,
    decisions: np.ndarray,
    exits: np.ndarray,
    window: Window,
    horizon: int,
    scenarios: Sequence[Scenario] = SCENARIOS,
) -> Run:
    """Avalia operações já escolhidas em cada cenário."""
    if len(decisions) and (
        decisions[0] < window.start
        or exits[-1] + 1 >= window.stop
        or (exits <= decisions).any()
        or (decisions[1:] <= exits[:-1]).any()
    ):
        raise ValueError("operações fora da janela ou sobrepostas")
    paths = {}
    for scenario in scenarios:
        returns, entry, fallbacks = trade_returns(market, decisions, exits, scenario)
        equity = _equity(market.close, window, decisions, exits, entry, returns, scenario)
        paths[scenario.name] = Path(
            returns=returns,
            held_minutes=int((exits - decisions).sum()),
            equity=equity,
            day_last=_day_last(market.minute_ts_us[window.start : window.stop]),
            fallbacks=fallbacks,
        )
    return Run(window, horizon, decisions, exits, paths)


def simulate(
    market: Market,
    signal: np.ndarray,
    horizon: int,
    window: Window,
    scenarios: Sequence[Scenario] = SCENARIOS,
) -> Run:
    """Uma estratégia (um sinal por minuto) numa janela e num horizonte (RF-251 a RF-254)."""
    decisions = select_trades(signal, market.eligible[horizon], horizon, window)
    return run_trades(market, decisions, decisions + horizon, window, horizon, scenarios)


def chain(paths: Sequence[Path]) -> Path:
    """Junta janelas consecutivas, como as dobras de teste do desenvolvimento (RF-255).

    O capital de cada janela continua de onde a anterior terminou."""
    if not paths:
        raise ValueError("nenhuma janela para juntar")
    equities, day_last, offset, scale = [], [], 0, 1.0
    for path in paths:
        equities.append(path.equity * scale)
        day_last.append(path.day_last + offset)
        offset += len(path.equity)
        scale *= float(path.equity[-1])
    return Path(
        returns=np.concatenate([p.returns for p in paths]),
        held_minutes=sum(p.held_minutes for p in paths),
        equity=np.concatenate(equities),
        day_last=np.concatenate(day_last),
        fallbacks=sum(p.fallbacks for p in paths),
    )


def _equity(
    close: np.ndarray,
    window: Window,
    decisions: np.ndarray,
    exits: np.ndarray,
    entry: np.ndarray,
    returns: np.ndarray,
    scenario: Scenario,
) -> np.ndarray:
    """Capital no fechamento de cada minuto da janela (doc 04 §10.1; DEC-38).

    Sem posição, é o capital realizado. Com posição aberta (minutos t + 1 a t + H), é o valor de
    liquidação: capital na entrada · c · (1 - s)(1 - f)² / (P_entrada (1 + s)); um minuto sem
    fechamento repete a última marca. A saída, no minuto t + 1 + H, já vale o realizado.
    """
    minutes = window.stop - window.start
    after = np.cumprod(1 + returns)
    realized = np.concatenate([[1.0], after])
    exit_minute = exits + 1 - window.start
    equity = realized[np.searchsorted(exit_minute, np.arange(minutes), side="right")]
    lengths = exits - decisions
    total = int(lengths.sum())
    if total:
        trade = np.repeat(np.arange(len(decisions)), lengths)
        starts = np.cumsum(lengths) - lengths
        offset = np.arange(total) - np.repeat(starts, lengths)
        minute = decisions[trade] + 1 + offset
        before = realized[:-1][trade]
        marks = before * close[minute] * (scenario.keep / (entry * scenario.pay))[trade]
        first_missing = (offset == 0) & ~np.isfinite(marks)
        marks[first_missing] = before[first_missing]  # a última marca é o capital antes da entrada
        idx = np.where(np.isfinite(marks), np.arange(total), 0)
        np.maximum.accumulate(idx, out=idx)
        equity[minute - window.start] = marks[idx]
    return equity


def _day_last(minute_ts_us: np.ndarray) -> np.ndarray:
    day = minute_ts_us // DAY_US
    return np.append(np.flatnonzero(np.diff(day)), len(day) - 1)


def _day_start_us(day: date) -> int:
    return int(datetime.combine(day, time(), UTC).timestamp()) * 1_000_000


def _day(ts_us: int) -> date:
    return datetime.fromtimestamp(ts_us / 1_000_000, UTC).date()


def _floats(table: pa.Table, name: str) -> np.ndarray:
    values = pc.cast(table[name], pa.float64()).to_numpy(zero_copy_only=False)
    return np.asarray(values, dtype=np.float64)
