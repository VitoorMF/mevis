import asyncio
import time
from pathlib import Path

import httpx

from conftest import FakeMarket, fast_config
from trader.ops.smoke import SmokeReport, run_smoke
from trader.sources.binance_ws import websockets_connector


def binance_like(configured: int, main_api: int) -> httpx.MockTransport:
    """`configured`: status do `rest_base_url`; `main_api`: status de api.binance.com."""

    def handler(request: httpx.Request) -> httpx.Response:
        status = main_api if request.url.host == "api.binance.com" else configured
        if status != 200:
            return httpx.Response(status)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": time.time_ns() // 1000})
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


def smoke(
    tmp_path: Path, configured: int, main_api: int, paused: frozenset[str] = frozenset()
) -> SmokeReport:
    async def main() -> SmokeReport:
        async with FakeMarket() as market:
            market.paused |= paused
            config = fast_config(tmp_path, market.url)
            transport = binance_like(configured, main_api)
            async with httpx.AsyncClient(transport=transport) as client:
                return await run_smoke(config, websockets_connector, 0.5, client)

    return asyncio.run(main())


def test_healthy_environment(tmp_path: Path) -> None:
    report = smoke(tmp_path, 200, 200)
    assert report.ok, report.lines
    assert any("WebSocket" in line and "trade" in line for line in report.lines)
    assert any("offset" in line for line in report.lines)
    assert not any(line.startswith("aviso") for line in report.lines)


def test_main_api_blocked_is_only_a_warning(tmp_path: Path) -> None:
    """Situação medida na us-east-2: api.binance.com 451, endpoints *.binance.vision 200."""
    report = smoke(tmp_path, 200, 451)
    assert report.ok, report.problems
    warnings = [line for line in report.lines if line.startswith("aviso")]
    assert any("bloqueio geográfico" in w for w in warnings)
    assert any("arquivos diários" in w for w in warnings)


def test_configured_endpoint_blocked_is_a_problem(tmp_path: Path) -> None:
    report = smoke(tmp_path, 451, 451)
    assert not report.ok
    assert any("bloqueio geográfico" in p for p in report.problems)


def test_missing_stream_is_a_problem(tmp_path: Path) -> None:
    report = smoke(tmp_path, 200, 200, frozenset({"btcusdt@depth@100ms"}))
    assert any("btcusdt@depth@100ms" in p for p in report.problems)
