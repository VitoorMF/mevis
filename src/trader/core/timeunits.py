"""Marcas de tempo: inteiros de microssegundos desde a época Unix, UTC (doc 06 §4.1)."""

from datetime import date
from enum import Enum

MINUTE_US = 60_000_000
SECOND_US = 1_000_000


class TimeUnit(Enum):
    MILLISECOND = "ms"
    MICROSECOND = "us"


class TimestampRangeError(ValueError):
    """Valor fora da faixa plausível para a unidade declarada."""


# Faixas válidas por unidade (doc 06 §4.1): de meados de 2017 até ~2100.
_VALID_RANGE = {
    TimeUnit.MILLISECOND: (1_500_000_000_000, 4_100_000_000_000),
    TimeUnit.MICROSECOND: (1_500_000_000_000_000, 4_100_000_000_000_000),
}

# Arquivos públicos de spot passaram a usar microssegundos em 01/01/2025 (SRS FV-04).
ARCHIVE_MICROSECONDS_SINCE = date(2025, 1, 1)


def to_us(value: int, unit: TimeUnit) -> int:
    """Converte uma marca de tempo da unidade declarada para microssegundos.

    A unidade nunca é inferida: um valor fora da faixa da unidade declarada é rejeitado.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TimestampRangeError(f"marca de tempo não inteira: {value!r}")
    low, high = _VALID_RANGE[unit]
    if not low <= value <= high:
        raise TimestampRangeError(f"{value} fora da faixa válida para {unit.value}")
    return value * 1000 if unit is TimeUnit.MILLISECOND else value


def archive_unit(file_date: date) -> TimeUnit:
    """Unidade de tempo dos arquivos públicos de spot, conforme a data do arquivo."""
    if file_date >= ARCHIVE_MICROSECONDS_SINCE:
        return TimeUnit.MICROSECOND
    return TimeUnit.MILLISECOND


def floor_minute(ts_us: int) -> int:
    """Início do minuto UTC que contém `ts_us`."""
    return ts_us - ts_us % MINUTE_US
