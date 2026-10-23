import asyncio

import httpx
import pytest

from conftest import FakeClock
from trader.sources.binance_rest import (
    BinanceRest,
    IpBannedError,
    RateLimitedError,
    RestError,
    WeightLimiter,
    depth_weight,
)


class TestWeightLimiter:
    def test_sliding_window(self) -> None:
        clock = FakeClock()
        limiter = WeightLimiter(100, clock)
        assert limiter.try_acquire(60) == 0
        clock.advance(10)
        assert limiter.try_acquire(30) == 0
        assert limiter.try_acquire(20) == pytest.approx(50.0)
        clock.advance(50)
        assert limiter.try_acquire(20) == 0

    def test_weight_above_budget_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError):
            WeightLimiter(100, FakeClock()).try_acquire(101)

    def test_block_for(self) -> None:
        clock = FakeClock()
        limiter = WeightLimiter(100, clock)
        limiter.block_for(7)
        assert limiter.blocked
        assert limiter.try_acquire(1) == pytest.approx(7.0)
        clock.advance(7)
        assert not limiter.blocked
        assert limiter.try_acquire(1) == 0


def test_depth_weight() -> None:
    assert [depth_weight(n) for n in (20, 500, 1000, 5000)] == [5, 25, 50, 250]


def rest_with(
    handler: httpx.MockTransport, clock: FakeClock | None = None
) -> tuple[BinanceRest, WeightLimiter, httpx.AsyncClient]:
    clock = clock or FakeClock()
    limiter = WeightLimiter(1200, clock)
    client = httpx.AsyncClient(transport=handler)
    return BinanceRest("https://fake.test/", limiter, clock, client), limiter, client


def test_success_returns_body_and_sends_microsecond_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"serverTime":1792779042655963}')

    async def main() -> None:
        rest, _, client = rest_with(httpx.MockTransport(handler))
        async with client:
            response = await rest.server_time()
        assert response.channel == "rest:time"
        assert response.body == b'{"serverTime":1792779042655963}'

    asyncio.run(main())
    assert seen[0].url == "https://fake.test/api/v3/time"
    assert seen[0].headers["X-MBX-TIME-UNIT"] == "MICROSECOND"


def test_depth_request_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}")

    async def main() -> None:
        rest, _, client = rest_with(httpx.MockTransport(handler))
        async with client:
            await rest.depth("BTCUSDT", 5000)

    asyncio.run(main())
    assert dict(seen[0].url.params) == {"symbol": "BTCUSDT", "limit": "5000"}


@pytest.mark.parametrize(
    ("status", "error"), [(429, RateLimitedError), (418, IpBannedError), (500, RestError)]
)
def test_error_statuses(status: int, error: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "7"})

    async def main() -> WeightLimiter:
        rest, limiter, client = rest_with(httpx.MockTransport(handler))
        async with client:
            with pytest.raises(error):
                await rest.server_time()
        return limiter

    limiter = asyncio.run(main())
    assert limiter.blocked is (status in (418, 429))


def test_transport_failure_becomes_rest_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    async def main() -> None:
        rest, _, client = rest_with(httpx.MockTransport(handler))
        async with client:
            with pytest.raises(RestError):
                await rest.server_time()

    asyncio.run(main())
