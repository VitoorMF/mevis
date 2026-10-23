"""Detecção de lacunas e de silêncio por canal (RF-111, RF-151, RF-152, RF-194).

Classes puras: quem chama fornece ids e relógios; nada aqui lê o tempo do sistema.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from trader.core.timeunits import MINUTE_US


class SeqStatus(Enum):
    OK = "ok"
    GAP = "gap"
    REPEATED = "repeated"
    """Id já visto ou anterior ao último (sobreposição na rotação ou retransmissão)."""


@dataclass(frozen=True, slots=True)
class IdGap:
    first_missing: int
    last_missing: int

    @property
    def size(self) -> int:
        return self.last_missing - self.first_missing + 1


@dataclass(frozen=True, slots=True)
class SeqResult:
    status: SeqStatus
    gap: IdGap | None = None


_OK = SeqResult(SeqStatus.OK)
_REPEATED = SeqResult(SeqStatus.REPEATED)


class TradeIdTracker:
    """Ids de negócio devem ser consecutivos dentro de uma sessão (RF-151)."""

    def __init__(self, last_id: int | None = None) -> None:
        self.last_id = last_id

    def observe(self, trade_id: int) -> SeqResult:
        last = self.last_id
        if last is not None and trade_id <= last:
            return _REPEATED
        self.last_id = trade_id
        if last is None or trade_id == last + 1:
            return _OK
        return SeqResult(SeqStatus.GAP, IdGap(last + 1, trade_id - 1))


class DiffContinuityTracker:
    """Continuidade do diferencial do livro: `U` da mensagem = `u` anterior + 1 (RF-194).

    Sobreposição (`U` ≤ `u` anterior < `u`) é aceita; mensagem inteiramente antiga é repetição.
    """

    def __init__(self, last_final_id: int | None = None) -> None:
        self.last_final_id = last_final_id

    def observe(self, first_update_id: int, final_update_id: int) -> SeqResult:
        last = self.last_final_id
        if last is not None and final_update_id <= last:
            return _REPEATED
        self.last_final_id = final_update_id
        if last is None or first_update_id <= last + 1:
            return _OK
        return SeqResult(SeqStatus.GAP, IdGap(last + 1, first_update_id - 1))


class MinuteSequenceTracker:
    """Candles fechados devem chegar um por minuto UTC (RF-152)."""

    def __init__(self, last_open_ts_us: int | None = None) -> None:
        self.last_open_ts_us = last_open_ts_us

    def observe(self, open_ts_us: int) -> list[int]:
        """Devolve os horários de abertura ausentes entre o último candle e este."""
        if open_ts_us % MINUTE_US:
            raise ValueError(f"abertura fora do início do minuto: {open_ts_us}")
        last = self.last_open_ts_us
        if last is not None and open_ts_us <= last:
            return []
        self.last_open_ts_us = open_ts_us
        if last is None:
            return []
        return list(range(last + MINUTE_US, open_ts_us, MINUTE_US))


def missing_minutes(open_times_us: Iterable[int], start_us: int, end_us: int) -> list[int]:
    """Minutos de `[start_us, end_us)` sem candle, para auditoria em lote."""
    first = start_us - start_us % MINUTE_US
    if first < start_us:
        first += MINUTE_US
    present = set(open_times_us)
    return [ts for ts in range(first, end_us, MINUTE_US) if ts not in present]


class SilenceMonitor:
    """Canais sem mensagens além do limite configurado (RF-111).

    Usa um relógio monotônico fornecido pelo chamador (doc 06 §4.2), em microssegundos.
    """

    def __init__(self, limits_us: Mapping[str, int], started_mono_us: int) -> None:
        self._limits = dict(limits_us)
        self._last_seen = dict.fromkeys(self._limits, started_mono_us)

    def touch(self, channel: str, now_mono_us: int) -> None:
        if channel in self._last_seen:
            self._last_seen[channel] = now_mono_us

    def silent(self, now_mono_us: int) -> list[str]:
        return [
            channel
            for channel, limit in self._limits.items()
            if now_mono_us - self._last_seen[channel] > limit
        ]

    def age_us(self, channel: str, now_mono_us: int) -> int:
        return now_mono_us - self._last_seen[channel]
