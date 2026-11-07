"""Janelas fixas sobre a série de minutos (doc 04 §5.1, regra 6). Sem E/S.

O valor em t depende só dos minutos da janela e é calculado sempre na mesma ordem, do minuto mais
antigo ao mais novo: o resultado é o mesmo, bit a bit, sobre a série inteira ou sobre os últimos
1.500 minutos (RF-206). Por isso as somas em ponto flutuante não usam somas acumuladas da série,
cujo arredondamento dependeria de onde ela começa; só as contagens, que são inteiras e exatas.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def lag(x: np.ndarray, k: int) -> np.ndarray:
    """x[t - k]; as primeiras k posições ficam NaN (ponto flutuante) ou False (booleano)."""
    out = np.empty_like(x)
    if k == 0:
        out[:] = x
        return out
    out[:k] = False if x.dtype == np.bool_ else np.nan
    out[k:] = x[:-k]
    return out


def window_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Soma dos w minutos que terminam em t; NaN antes de haver w minutos. Aceita matrizes."""
    n = x.shape[0]
    out = np.full(x.shape, np.nan)
    if n >= w:
        acc = np.zeros((n - w + 1, *x.shape[1:]))
        for k in range(w):
            acc += x[k : n - w + 1 + k]
        out[w - 1 :] = acc
    return out


def window_count(ok: np.ndarray, w: int) -> np.ndarray:
    """Quantos dos w minutos que terminam em t são verdadeiros; -1 antes de haver w minutos."""
    cumulative = np.concatenate(([0], np.cumsum(ok, dtype=np.int64)))
    out = np.full(ok.shape[0], -1, dtype=np.int64)
    if ok.shape[0] >= w:
        out[w - 1 :] = cumulative[w:] - cumulative[:-w]
    return out


def window_max(x: np.ndarray, w: int) -> np.ndarray:
    return _extreme(x, w, maximum=True)


def window_min(x: np.ndarray, w: int) -> np.ndarray:
    return _extreme(x, w, maximum=False)


def _extreme(x: np.ndarray, w: int, *, maximum: bool) -> np.ndarray:
    out = np.full(x.shape[0], np.nan)
    if x.shape[0] >= w:
        view = sliding_window_view(x, w)
        out[w - 1 :] = view.max(axis=1) if maximum else view.min(axis=1)
    return out


def all_valid(ok_prev: np.ndarray, ok_now: np.ndarray, k: int) -> np.ndarray:
    """Todos os minutos de t - k a t válidos: t disponível em t_d, os anteriores até t_d."""
    previous = lag(window_count(ok_prev, k) == k, 1) if k else np.ones_like(ok_now)
    ok: np.ndarray = ok_now & previous
    return ok


def enough_valid(count: np.ndarray, w: int, percent: int) -> np.ndarray:
    """Pelo menos `percent`% de minutos válidos numa janela de w minutos, em inteiros exatos."""
    return count * 100 >= percent * w
