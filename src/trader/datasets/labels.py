"""Alvo, pontos elegíveis e tabela do dataset (doc 04 §4.3, §4.4 e §11; RF-223 a RF-226).

O alvo de um minuto t, no horizonte H, é o resultado da operação simulada com entrada no preço de
execução base do minuto t + 1 e saída no do minuto t + 1 + H, com taxa e deslizamento cobrados
contra a operação (DEC-36). Os preços passam de DECIMAL para FLOAT64 só aqui, como nas features
(RF-212). As colunas do alvo são informação do futuro em relação à decisão: servem para treinar e
avaliar, nunca como entrada do modelo.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pyarrow as pa

from trader.aggregate.schema import EXEC_OFFSETS_MS
from trader.features.compute import DECISION_DELAY_US, FAMILIES, FeatureFrame

HORIZONS = (15, 60)
"""Horizontes em minutos (doc 04 §4.2)."""
FEE = 0.001
"""Taxa por lado: 0,10% (doc 04 §9)."""
SLIPPAGE = 0.0001
"""Deslizamento por lado: 0,01% (doc 04 §9)."""
LATENCY_MS = 500
"""Latência de execução base depois do instante de decisão (doc 04 §9)."""

TRACK_FAMILIES = {"A": FAMILIES[:3], "B": FAMILIES}
"""Famílias do degrau mais alto de cada trilha: B6 na A (sem livro), B7 na B (RF-225)."""


@dataclass(frozen=True)
class LabelParams:
    horizons: tuple[int, ...] = HORIZONS
    fee: float = FEE
    slippage: float = SLIPPAGE
    latency_ms: int = LATENCY_MS

    @property
    def price_column(self) -> str:
        """Coluna de `minute_trades` com o preço de execução dessa latência (doc 06 §5.6)."""
        offset = DECISION_DELAY_US // 1000 + self.latency_ms
        if offset not in EXEC_OFFSETS_MS:
            raise ValueError(f"sem preço de execução a {offset} ms do início do minuto")
        return f"exec_price_{offset}ms"

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"horizons": list(self.horizons), "price_column": self.price_column}


@dataclass(frozen=True)
class Labels:
    entry_price: np.ndarray
    """Preço de execução do minuto t + 1; NaN onde não há."""
    exit_price: dict[int, np.ndarray]
    """Por horizonte, preço de execução do minuto t + 1 + H; NaN onde não há."""
    net_return: dict[int, np.ndarray]
    """Por horizonte, retorno líquido; NaN onde o alvo não é válido."""
    valid: dict[int, np.ndarray]
    """Por horizonte, validade do alvo (RF-224)."""


def compute_labels(price: np.ndarray, trades_valid: np.ndarray, params: LabelParams) -> Labels:
    """Alvo de cada minuto de uma série contígua (RF-223, RF-224).

    `price` é o preço de execução base de cada minuto (NaN onde não há) e `trades_valid`, a
    validade dos negócios do minuto, já com a exceção registrada (RF-203). A série termina no fim
    do período: um ponto cuja saída cairia depois dele fica sem alvo.
    """
    n = len(price)
    if trades_valid.shape != (n,):
        raise ValueError("preço e validade com tamanhos diferentes")
    price = np.where(price > 0, price, np.nan)
    invalid = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(~trades_valid, dtype=np.int64, out=invalid[1:])
    entry = _ahead(price, 1)
    keep = (1 - params.slippage) * (1 - params.fee) ** 2
    pay = 1 + params.slippage
    exits, returns, valids = {}, {}, {}
    for h in params.horizons:
        exit_price = _ahead(price, 1 + h)
        ok = np.zeros(n, dtype=bool)
        last = n - 1 - (1 + h)  # último t com a saída dentro do período
        if last >= 0:
            t = np.arange(last + 1)
            ok[: last + 1] = invalid[t + 2 + h] == invalid[t + 1]  # minutos t + 1 .. t + 1 + H
        ok &= np.isfinite(entry) & np.isfinite(exit_price)
        with np.errstate(invalid="ignore"):
            r = exit_price * keep / (entry * pay) - 1
        exits[h] = exit_price
        returns[h] = np.where(ok, r, np.nan)
        valids[h] = ok
    return Labels(entry, exits, returns, valids)


def _ahead(x: np.ndarray, k: int) -> np.ndarray:
    """`out[t] = x[t + k]`, NaN depois do fim."""
    out = np.full_like(x, np.nan)
    if k < len(x):
        out[: len(x) - k] = x[k:]
    return out


def eligible_points(frame: FeatureFrame, labels: Labels, track: str) -> dict[int, np.ndarray]:
    """Por horizonte: válido em todas as famílias do degrau mais alto da trilha e com alvo válido.

    B2, B3 e os degraus B4 a B7 usam estes mesmos pontos (RF-225, DEC-36)."""
    top = np.logical_and.reduce([frame.valid[family] for family in TRACK_FAMILIES[track]])
    return {h: top & labels.valid[h] for h in labels.valid}


def dataset_table(frame: FeatureFrame, labels: Labels, track: str) -> pa.Table:
    """Uma linha por minuto: features, validade por família e, por horizonte, o alvo (RF-226)."""
    table = frame.to_table()
    eligible = eligible_points(frame, labels, track)
    table = table.append_column("entry_price", _floats(labels.entry_price))
    for h in labels.valid:
        valid = labels.valid[h]
        r = labels.net_return[h]
        columns = {
            f"exit_price_h{h}": _floats(labels.exit_price[h]),
            f"net_return_h{h}": _floats(r),
            f"target_h{h}": pa.array(np.where(valid, r > 0, False), pa.bool_(), mask=~valid),
            f"target_valid_h{h}": pa.array(valid, pa.bool_()),
            f"eligible_h{h}": pa.array(eligible[h], pa.bool_()),
        }
        for name, values in columns.items():
            table = table.append_column(name, values)
    return table


def _floats(x: np.ndarray) -> pa.Array:
    return pa.array(x, pa.float64(), from_pandas=True)
