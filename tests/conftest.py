"""Dublês de teste: relógio controlável, mercado WebSocket local e REST simulada."""

import asyncio
import contextlib
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self

import httpx
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from trader.ingest.collector import Collector
from trader.ingest.raw_writer import RawWriter
from trader.ops.clock import SystemClock
from trader.ops.config import CollectorConfig
from trader.ops.health import health_path, read_health
from trader.ops.metrics import NullMetrics
from trader.ops.state import StateStore, StoredGap
from trader.sources.binance_rest import BinanceRest, WeightLimiter
from trader.sources.binance_ws import websockets_connector

STREAMS = (
    "btcusdt@trade",
    "btcusdt@kline_1m",
    "btcusdt@depth20@100ms",
    "btcusdt@depth@100ms",
)


class FakeClock:
    def __init__(self, now_us: int = 1_792_779_000_000_000, mono_us: int = 0) -> None:
        self.now = now_us
        self.mono = mono_us

    def now_us(self) -> int:
        return self.now

    def mono_us(self) -> int:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1e6)
        self.mono += int(seconds * 1e6)


class FakeMarket:
    """Servidor WebSocket local que transmite os quatro streams a todos os clientes a cada tick.

    Como na Binance, clientes conectados ao mesmo tempo recebem a mesma sequência de ids.
    """

    def __init__(self, tick_s: float = 0.01) -> None:
        self.tick_s = tick_s
        self.trade_id = 1000
        self.update_id = 50_000
        self.connections = 0
        self.paused: set[str] = set()
        self.clients: set[ServerConnection] = set()
        self.url = ""
        self._server: Server | None = None
        self._ticker: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        port = next(iter(self._server.sockets)).getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"
        self._ticker = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._ticker is not None and self._server is not None
        self._ticker.cancel()
        await asyncio.gather(self._ticker, return_exceptions=True)
        self._server.close()
        await self._server.wait_closed()

    def skip_trades(self, count: int) -> None:
        self.trade_id += count

    def skip_updates(self, count: int) -> None:
        self.update_id += count

    async def drop_clients(self) -> None:
        for ws in list(self.clients):
            await ws.close()

    async def _handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        self.clients.add(ws)
        try:
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    async def _run(self) -> None:
        while True:
            await self._tick()
            await asyncio.sleep(self.tick_s)

    async def _tick(self) -> None:
        now = time.time_ns() // 1000
        self.trade_id += 1
        first_update = self.update_id + 1
        self.update_id += 3
        book = {
            "lastUpdateId": self.update_id,
            "bids": [["81524.00000000", "1.20000000"]],
            "asks": [["81524.01000000", "0.30000000"]],
        }
        messages: dict[str, dict[str, Any]] = {
            "btcusdt@trade": {
                "e": "trade",
                "E": now,
                "s": "BTCUSDT",
                "t": self.trade_id,
                "p": "81524.01000000",
                "q": "0.00008000",
                "T": now,
                "m": False,
                "M": True,
            },
            "btcusdt@kline_1m": {"e": "kline", "E": now, "s": "BTCUSDT", "k": {"x": False}},
            "btcusdt@depth20@100ms": book,
            "btcusdt@depth@100ms": {
                "e": "depthUpdate",
                "E": now,
                "s": "BTCUSDT",
                "U": first_update,
                "u": self.update_id,
                "b": [],
                "a": [],
            },
        }
        for stream, data in messages.items():
            if stream in self.paused:
                continue
            text = json.dumps({"stream": stream, "data": data}, separators=(",", ":"))
            for ws in list(self.clients):
                with contextlib.suppress(ConnectionClosed):
                    await ws.send(text)


class FakeRest:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        assert request.headers["X-MBX-TIME-UNIT"] == "MICROSECOND"
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": time.time_ns() // 1000})
        if path == "/api/v3/depth":
            return httpx.Response(
                200,
                json={
                    "lastUpdateId": 1,
                    "bids": [["81524.00000000", "1.00000000"]],
                    "asks": [["81524.01000000", "1.00000000"]],
                },
            )
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": []})
        return httpx.Response(404)


def fast_config(data_dir: Path, ws_url: str, **overrides: Any) -> CollectorConfig:
    """Configuração com intervalos curtos, para os testes não demorarem."""
    base = CollectorConfig(
        data_dir=data_dir,
        ws_base_urls=(ws_url,),
        rest_base_url="https://fake.binance.test",
        open_timeout_s=2.0,
        silence_limits_s={
            "trade": 5.0,
            "kline_1m": 5.0,
            "depth20@100ms": 1.0,
            "depth@100ms": 1.0,
        },
        backoff_initial_s=0.05,
        backoff_max_s=0.2,
        handover_overlap_s=0.1,
        handover_timeout_s=2.0,
        snapshot_min_interval_s=0.01,
        clock_probe_interval_s=0.5,
        flush_interval_s=0.05,
        health_interval_s=0.05,
        metrics_interval_s=0.2,
    )
    return replace(base, **overrides).validate()


@dataclass
class Run:
    config: CollectorConfig
    state: StateStore
    rest: FakeRest
    task: "asyncio.Task[None]"

    def health(self) -> dict[str, Any]:
        return read_health(health_path(self.config.data_dir)) or {}

    def channel_messages(self) -> dict[str, int]:
        channels = self.health().get("channels", {})
        return {stream: info["messages"] for stream, info in channels.items()}

    def gaps(self, channel: str | None = None) -> list[StoredGap]:
        gaps = self.state.gaps_since(0)
        return [g for g in gaps if channel is None or g.record.channel == channel]

    def raw_dir(self) -> Path:
        return self.config.data_dir / "raw" / "binance-spot" / "btcusdt"

    async def eventually(self, predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not predicate():
            if self.task.done():
                self.task.result()
                raise AssertionError("o coletor parou antes da condição")
            if time.monotonic() > deadline:
                raise AssertionError("condição não atingida no prazo")
            await asyncio.sleep(0.02)

    async def flowing(self, minimum: int = 5) -> None:
        await self.eventually(
            lambda: all(self.channel_messages().get(s, 0) >= minimum for s in STREAMS)
        )


async def collect_while(
    config: CollectorConfig,
    rest: FakeRest,
    scenario: Callable[[Run], Awaitable[None]],
    timeout_s: float = 15.0,
) -> Run:
    """Roda o coletor de verdade (WebSocket local, REST simulada) enquanto o cenário executa."""
    stop = asyncio.Event()
    clock = SystemClock()
    state = StateStore(config.data_dir / "state.db")
    transport = httpx.MockTransport(rest.handler)
    async with httpx.AsyncClient(transport=transport) as client:
        collector = Collector(
            config,
            clock=clock,
            writer=RawWriter(config.data_dir, config.symbol),
            state=state,
            rest=BinanceRest(config.rest_base_url, WeightLimiter(1200, clock), clock, client),
            metrics=NullMetrics(),
            connector=websockets_connector,
            code_version="test",
        )
        run = Run(config, state, rest, asyncio.create_task(collector.run(stop)))
        try:
            await asyncio.wait_for(scenario(run), timeout_s)
        finally:
            stop.set()
            await asyncio.wait_for(run.task, 10)
    return run
