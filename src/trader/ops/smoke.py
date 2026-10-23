"""Teste de fumaça de conectividade (doc 03 §9.4): roda no host antes de ligar a coleta.

Confere, a partir da região onde está, se a Binance responde (sem HTTP 451), se todos os streams
chegam, e mede latência e offset de relógio. Não grava nada.

São obrigatórios os endpoints configurados (`rest_base_url` e o primeiro de `ws_base_urls`). A
API principal (`api.binance.com`) é só informativa: nos EUA ela responde 451, e sem ela as
lacunas de negócios são recuperadas pelos arquivos diários (doc 06 §9).
"""

import asyncio
import json
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

from trader.ops.config import CollectorConfig
from trader.sources.binance_ws import Connector, stream_url

MAIN_REST = "https://api.binance.com"
PROBES = 5


@dataclass
class SmokeReport:
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, line: str, *, problem: bool = False, warning: bool = False) -> None:
        prefix = "FALHA  " if problem else "aviso  " if warning else "ok     "
        self.lines.append(prefix + line)
        if problem:
            self.problems.append(line)


async def run_smoke(
    config: CollectorConfig, connector: Connector, duration_s: float, client: httpx.AsyncClient
) -> SmokeReport:
    report = SmokeReport()
    await _probe_rest(config.rest_base_url, client, report, required=True)
    if config.rest_base_url != MAIN_REST:
        await _probe_rest(MAIN_REST, client, report, required=False)
    await _probe_historical_trades(client, report)
    for index, endpoint in enumerate(config.ws_base_urls):
        # O endpoint principal recebe o tempo todo; os alternativos, só uma checagem curta.
        seconds = duration_s if index == 0 else min(duration_s, 10.0)
        await _probe_ws(endpoint, config, connector, seconds, report)
    return report


async def _probe_rest(
    base: str, client: httpx.AsyncClient, report: SmokeReport, *, required: bool
) -> None:
    samples = []
    for _ in range(PROBES):
        sent = time.time_ns() // 1000
        try:
            response = await client.get(
                f"{base}/api/v3/time", headers={"X-MBX-TIME-UNIT": "MICROSECOND"}
            )
        except httpx.HTTPError as exc:
            report.add(f"REST {base}: {exc!r}", problem=required, warning=not required)
            return
        recv = time.time_ns() // 1000
        if response.status_code != 200:
            hint = " (bloqueio geográfico)" if response.status_code == 451 else ""
            report.add(
                f"REST {base}: HTTP {response.status_code}{hint}",
                problem=required,
                warning=not required,
            )
            return
        server = int(json.loads(response.content)["serverTime"])
        samples.append((recv - sent, server - (sent + recv) // 2))
    rtt, offset = min(samples)  # a amostra de menor ida e volta tem o offset mais confiável
    median_rtt = statistics.median(s[0] for s in samples)
    report.add(
        f"REST {base}: ida e volta mín. {rtt / 1000:.0f} ms (mediana {median_rtt / 1000:.0f} ms),"
        f" offset {offset / 1000:+.1f} ms"
    )


async def _probe_historical_trades(client: httpx.AsyncClient, report: SmokeReport) -> None:
    url = f"{MAIN_REST}/api/v3/historicalTrades"
    try:
        response = await client.get(url, params={"symbol": "BTCUSDT", "limit": "1"})
    except httpx.HTTPError as exc:
        report.add(f"historicalTrades: {exc!r}", warning=True)
        return
    if response.status_code == 200:
        report.add("historicalTrades sem chave: HTTP 200 (recuperação imediata disponível)")
    else:
        report.add(
            f"historicalTrades: HTTP {response.status_code}; lacunas de negócios só pelos"
            " arquivos diários (doc 06 §9)",
            warning=True,
        )


async def _probe_ws(
    endpoint: str,
    config: CollectorConfig,
    connector: Connector,
    seconds: float,
    report: SmokeReport,
) -> None:
    try:
        conn = await asyncio.wait_for(
            connector(stream_url(endpoint, config.streams)), config.open_timeout_s
        )
    except Exception as exc:
        hint = " (bloqueio geográfico)" if "451" in repr(exc) else ""
        report.add(f"WebSocket {endpoint}: {exc!r}{hint}", problem=True)
        return
    counts: Counter[str] = Counter()
    delays = []
    deadline = time.monotonic() + seconds
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                data = await asyncio.wait_for(conn.recv(), timeout=remaining)
            except TimeoutError:
                break
            recv = time.time_ns() // 1000
            message = json.loads(data)
            stream = message.get("stream", "?")
            counts[stream] += 1
            event = message.get("data", {}).get("E")
            if stream.endswith("@trade") and isinstance(event, int):
                delays.append(recv - event)
    except Exception as exc:
        report.add(
            f"WebSocket {endpoint}: caiu após {sum(counts.values())} msgs: {exc!r}", problem=True
        )
    finally:
        await conn.close()
    missing = [s for s in config.streams if counts[s] == 0]
    rates = ", ".join(f"{s.split('@', 1)[1]} {counts[s] / seconds:.1f}/s" for s in config.streams)
    delay_text = (
        f"; atraso evento→recebimento p50 {statistics.median(delays) / 1000:.0f} ms (bruto)"
        if delays
        else ""
    )
    report.add(
        f"WebSocket {endpoint} ({seconds:.0f} s): {rates}{delay_text}", problem=bool(missing)
    )
    if missing:
        report.add(f"WebSocket {endpoint}: sem mensagens em {missing}", problem=True)
