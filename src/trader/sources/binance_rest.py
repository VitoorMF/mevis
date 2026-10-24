"""Cliente REST de dados de mercado da Binance com teto próprio de peso (doc 06 §3.4-3.5)."""

import asyncio
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from trader.ops.clock import Clock

WINDOW_US = 60_000_000


class RestError(Exception):
    """Falha de uma chamada REST; quem chama decide se tenta de novo."""


class RateLimitedError(RestError):
    """HTTP 429: a fonte pediu para esperar `retry_after_s`."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"HTTP 429; aguardar {retry_after_s} s")
        self.retry_after_s = retry_after_s


class IpBannedError(RestError):
    """HTTP 418: IP banido. Toda chamada REST fica bloqueada até o prazo (RF-145)."""


def depth_weight(limit: int) -> int:
    if limit <= 100:
        return 5
    if limit <= 500:
        return 25
    if limit <= 1000:
        return 50
    return 250


class WeightLimiter:
    """Janela deslizante de 60 s sobre o peso consumido, mais bloqueio após 429/418."""

    def __init__(self, budget_per_min: int, clock: Clock) -> None:
        self._budget = budget_per_min
        self._clock = clock
        self._spent: deque[tuple[int, int]] = deque()
        self._blocked_until_us = 0

    def try_acquire(self, weight: int) -> float:
        """Reserva `weight` e devolve 0, ou devolve quantos segundos esperar."""
        if weight > self._budget:
            raise ValueError(f"peso {weight} maior que o teto {self._budget}")
        now = self._clock.mono_us()
        if now < self._blocked_until_us:
            return (self._blocked_until_us - now) / 1e6
        while self._spent and now - self._spent[0][0] >= WINDOW_US:
            self._spent.popleft()
        used = sum(w for _, w in self._spent)
        if used + weight > self._budget:
            freed = 0
            for ts, w in self._spent:
                freed += w
                if used - freed + weight <= self._budget:
                    return (ts + WINDOW_US - now) / 1e6
        self._spent.append((now, weight))
        return 0.0

    async def acquire(self, weight: int) -> None:
        while (wait := self.try_acquire(weight)) > 0:
            await asyncio.sleep(wait)

    def block_for(self, seconds: float) -> None:
        until = self._clock.mono_us() + int(seconds * 1e6)
        self._blocked_until_us = max(self._blocked_until_us, until)

    @property
    def blocked(self) -> bool:
        return self._clock.mono_us() < self._blocked_until_us


@dataclass(frozen=True, slots=True)
class RestResponse:
    channel: str
    sent_ts_us: int
    recv_ts_us: int
    status: int
    body: bytes


class BinanceRest:
    def __init__(
        self, base_url: str, limiter: WeightLimiter, clock: Clock, client: httpx.AsyncClient
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._limiter = limiter
        self._clock = clock
        self._client = client

    async def server_time(self) -> RestResponse:
        return await self._get("/api/v3/time", {}, weight=1, channel="rest:time")

    async def depth(self, symbol: str, limit: int) -> RestResponse:
        params = {"symbol": symbol, "limit": str(limit)}
        return await self._get("/api/v3/depth", params, depth_weight(limit), "rest:depth")

    async def exchange_info(self, symbol: str) -> RestResponse:
        params = {"symbol": symbol}
        return await self._get("/api/v3/exchangeInfo", params, 20, "rest:exchangeInfo")

    async def _get(
        self, path: str, params: Mapping[str, str], weight: int, channel: str
    ) -> RestResponse:
        # Após 429/418, `acquire` espera o prazo indicado pela fonte antes de liberar (RF-145).
        await self._limiter.acquire(weight)
        sent = self._clock.now_us()
        try:
            response = await self._client.get(
                self._base_url + path,
                params=params,
                headers={"X-MBX-TIME-UNIT": "MICROSECOND"},
            )
        except httpx.HTTPError as exc:
            raise RestError(f"{path}: {exc!r}") from exc
        recv = self._clock.now_us()
        if response.status_code in (418, 429):
            retry_after = _retry_after(response)
            self._limiter.block_for(retry_after)
            if response.status_code == 418:
                raise IpBannedError(f"HTTP 418; bloqueado por {retry_after} s")
            raise RateLimitedError(retry_after)
        if response.status_code != 200:
            raise RestError(f"{path}: HTTP {response.status_code}")
        return RestResponse(channel, sent, recv, response.status_code, response.content)


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(1.0, float(response.headers.get("Retry-After", "60")))
    except ValueError:
        return 60.0


def get_klines_sync(
    client: httpx.Client, base_url: str, symbol: str, start_ms: int, end_ms: int
) -> RestResponse:
    """`/api/v3/klines` de 1 min (peso 2, até 1000 candles) para o pipeline, que é síncrono.

    Marcas de tempo da resposta em µs. Uma rodada faz poucas chamadas; depois de 429 ou 418,
    quem chama não faz mais nenhuma na rodada (RF-145).
    """
    params = {
        "symbol": symbol,
        "interval": "1m",
        "startTime": str(start_ms),
        "endTime": str(end_ms),
        "limit": "1000",
    }
    sent = time.time_ns() // 1000
    try:
        response = client.get(
            base_url + "/api/v3/klines",
            params=params,
            headers={"X-MBX-TIME-UNIT": "MICROSECOND"},
        )
    except httpx.HTTPError as exc:
        raise RestError(f"/api/v3/klines: {exc!r}") from exc
    recv = time.time_ns() // 1000
    if response.status_code == 418:
        raise IpBannedError(f"HTTP 418; bloqueado por {_retry_after(response)} s")
    if response.status_code == 429:
        raise RateLimitedError(_retry_after(response))
    if response.status_code != 200:
        raise RestError(f"/api/v3/klines: HTTP {response.status_code}")
    return RestResponse("rest:klines", sent, recv, response.status_code, response.content)
