"""Relógios injetáveis (doc 06 §4.2): parede para `recv_ts_us`, monotônico para intervalos."""

import time
from typing import Protocol


class Clock(Protocol):
    def now_us(self) -> int:
        """Relógio de parede, microssegundos desde a época Unix (UTC)."""
        ...

    def mono_us(self) -> int:
        """Relógio monotônico em microssegundos; só serve para medir intervalos."""
        ...


class SystemClock:
    def now_us(self) -> int:
        return time.time_ns() // 1000

    def mono_us(self) -> int:
        return time.monotonic_ns() // 1000
