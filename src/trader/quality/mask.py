"""Máscara de qualidade por canal e minuto (RF-159; doc 06 §8.2)."""

import bisect
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from trader.core.timeunits import MINUTE_US


class MaskStatus(Enum):
    """Do pior para o melhor; num mesmo minuto, prevalece o pior."""

    MISSING = "missing"
    INVALID = "invalid"
    LOW_CLOCK_CONFIDENCE = "low_clock_confidence"
    LATE = "late"
    RECOVERED = "recovered"
    OK = "ok"


_SEVERITY = {status: rank for rank, status in enumerate(MaskStatus)}


class Flag(Enum):
    """Fatos observados na normalização de um minuto de um canal."""

    DATA = "data"
    INVALID = "invalid"
    LATE = "late"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class GapSpan:
    channel: str
    start_ts_us: int
    end_ts_us: int
    status: str
    """`open`, `irrecoverable`, `partial` ou `recovered` (tabela `gaps`)."""


@dataclass(frozen=True, slots=True)
class MaskRow:
    channel: str
    minute_ts_us: int
    status: MaskStatus
    details: str


class ClockReference:
    """Offsets medidos (RF-125) para avaliar a confiança no horário de recebimento."""

    def __init__(self, measurements: Iterable[tuple[int, int]], window_us: int) -> None:
        ordered = sorted(measurements)
        self._times = [t for t, _ in ordered]
        self._offsets = [o for _, o in ordered]
        self._window_us = window_us

    def offset_at(self, ts_us: int) -> int | None:
        """Offset da medição mais próxima dentro da janela, ou `None` se não houver."""
        index = bisect.bisect_left(self._times, ts_us)
        best: tuple[int, int] | None = None
        for i in (index - 1, index):
            if 0 <= i < len(self._times):
                distance = abs(self._times[i] - ts_us)
                if distance <= self._window_us and (best is None or distance < best[0]):
                    best = (distance, self._offsets[i])
        return None if best is None else best[1]


def worst(statuses: Iterable[MaskStatus]) -> MaskStatus:
    return min(statuses, key=_SEVERITY.__getitem__, default=MaskStatus.OK)


def build_mask(
    *,
    hour_start_us: int,
    channels: Sequence[str],
    flags: Mapping[tuple[str, int], set[Flag]],
    gaps: Sequence[GapSpan],
    clock: ClockReference,
    offset_alert_us: int,
) -> list[MaskRow]:
    rows = []
    for channel in channels:
        channel_gaps = [g for g in gaps if g.channel == channel]
        for minute in range(hour_start_us, hour_start_us + 60 * MINUTE_US, MINUTE_US):
            found = flags.get((channel, minute), set())
            statuses = set()
            overlapping = [
                g
                for g in channel_gaps
                if g.start_ts_us < minute + MINUTE_US and g.end_ts_us > minute
            ]
            if Flag.DATA not in found or any(g.status != "recovered" for g in overlapping):
                statuses.add(MaskStatus.MISSING)
            if Flag.INVALID in found:
                statuses.add(MaskStatus.INVALID)
            offset = clock.offset_at(minute + MINUTE_US // 2)
            if offset is None or abs(offset) > offset_alert_us:
                statuses.add(MaskStatus.LOW_CLOCK_CONFIDENCE)
            if Flag.LATE in found:
                statuses.add(MaskStatus.LATE)
            if Flag.RECOVERED in found or overlapping:
                statuses.add(MaskStatus.RECOVERED)
            status = worst(statuses)
            details = ",".join(s.value for s in sorted(statuses, key=_SEVERITY.__getitem__))
            rows.append(MaskRow(channel, minute, status, details or MaskStatus.OK.value))
    return rows
