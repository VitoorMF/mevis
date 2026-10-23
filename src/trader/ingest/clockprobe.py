"""Offset entre o relógio do host e o da exchange (RF-125; doc 06 §4.2)."""

import json
from dataclasses import dataclass

from trader.core.timeunits import TimeUnit, to_us
from trader.sources.binance_rest import RestResponse


@dataclass(frozen=True, slots=True)
class ClockOffset:
    local_ts_us: int
    server_ts_us: int
    rtt_us: int
    offset_us: int
    """Horário do servidor - horário local, compensando metade do tempo de ida e volta."""


def measure_offset(response: RestResponse) -> ClockOffset:
    """Calcula o offset a partir de `/api/v3/time` pedido com `X-MBX-TIME-UNIT: MICROSECOND`."""
    payload = json.loads(response.body)
    server = to_us(int(payload["serverTime"]), TimeUnit.MICROSECOND)
    midpoint = (response.sent_ts_us + response.recv_ts_us) // 2
    return ClockOffset(
        local_ts_us=midpoint,
        server_ts_us=server,
        rtt_us=response.recv_ts_us - response.sent_ts_us,
        offset_us=server - midpoint,
    )
