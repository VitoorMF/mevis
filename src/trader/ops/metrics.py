"""Métricas via StatsD para o agente do CloudWatch (ADR-03; doc 03 §7.5).

UDP sem confirmação: falhas de envio nunca afetam a coleta.
"""

import contextlib
import socket
from typing import Protocol


class Metrics(Protocol):
    def gauge(self, name: str, value: float) -> None: ...

    def close(self) -> None: ...


class NullMetrics:
    """Usado localmente, sem agente."""

    def gauge(self, name: str, value: float) -> None:
        return None

    def close(self) -> None:
        return None


class StatsdMetrics:
    def __init__(self, host: str, port: int) -> None:
        self._address = (host, port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)

    def gauge(self, name: str, value: float) -> None:
        with contextlib.suppress(OSError):
            self._socket.sendto(f"{name}:{value:g}|g".encode(), self._address)

    def close(self) -> None:
        self._socket.close()
