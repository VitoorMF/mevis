"""Testes de integração do coletor contra um mercado WebSocket local (SRS UC-01 a UC-03)."""

import asyncio
import json
from pathlib import Path

from conftest import STREAMS, FakeMarket, FakeRest, Run, collect_while, fast_config
from trader.core.rawformat import decode
from trader.ops.health import health_path, read_health


def raw_records(directory: Path) -> list[tuple[str, int, bytes]]:
    records = []
    for path in sorted(directory.rglob("*.jsonl")):
        for line in path.read_bytes().splitlines(keepends=True):
            record = decode(line)
            records.append((record.session, record.seq, record.payload))
    return records


def test_collects_every_channel_into_sealed_raw_files(tmp_path: Path) -> None:
    async def scenario(run: Run) -> None:
        await run.flowing()
        await run.eventually(lambda: run.rest.calls["/api/v3/depth"] >= 1)

    async def main() -> Run:
        async with FakeMarket() as market:
            return await collect_while(fast_config(tmp_path, market.url), FakeRest(), scenario)

    run = asyncio.run(main())
    raw = run.raw_dir()
    assert not list(raw.rglob("*.open")), "todo arquivo precisa estar fechado após a parada"
    expected = {*STREAMS, "rest_depth", "rest_time", "rest_exchangeInfo"}
    assert expected <= {p.name for p in raw.iterdir()}
    for stream in STREAMS:
        records = raw_records(raw / stream)
        assert records
        assert all(json.loads(payload)["stream"] == stream for _, _, payload in records)
    sessions = run.state.sessions()
    assert len(sessions) == 1
    assert sessions[0].close_reason == "shutdown"
    assert run.gaps() == []
    health = read_health(health_path(tmp_path))
    assert health is not None and health["status"] == "stopped"


def test_trade_id_jump_is_recorded_as_recoverable_gap(tmp_path: Path) -> None:
    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.flowing()
                market.skip_trades(4)
                await run.eventually(lambda: bool(run.gaps("btcusdt@trade")))

            return await collect_while(fast_config(tmp_path, market.url), FakeRest(), scenario)

    run = asyncio.run(main())
    [gap] = run.gaps("btcusdt@trade")
    assert gap.record.cause == "trade_id_jump"
    assert gap.record.recoverable
    assert gap.status == "open"
    assert gap.record.first_missing_id is not None and gap.record.last_missing_id is not None
    assert gap.record.last_missing_id - gap.record.first_missing_id + 1 == 4


def test_diff_break_is_irrecoverable_and_requests_snapshot(tmp_path: Path) -> None:
    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.flowing()
                await run.eventually(lambda: run.rest.calls["/api/v3/depth"] >= 1)
                market.skip_updates(10)
                await run.eventually(lambda: bool(run.gaps("btcusdt@depth@100ms")))
                await run.eventually(lambda: run.rest.calls["/api/v3/depth"] >= 2)

            return await collect_while(fast_config(tmp_path, market.url), FakeRest(), scenario)

    run = asyncio.run(main())
    [gap] = run.gaps("btcusdt@depth@100ms")
    assert gap.record.cause == "diff_break"
    assert not gap.record.recoverable
    assert gap.status == "irrecoverable"


def test_reconnects_after_server_drop(tmp_path: Path) -> None:
    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.flowing()
                await market.drop_clients()
                market.skip_trades(3)
                await run.eventually(lambda: len(run.state.sessions()) >= 2)
                await run.eventually(lambda: bool(run.gaps("btcusdt@trade")))

            return await collect_while(fast_config(tmp_path, market.url), FakeRest(), scenario)

    run = asyncio.run(main())
    first = run.state.sessions()[0]
    assert first.close_reason is not None and first.close_reason.startswith("closed")
    assert run.gaps("btcusdt@trade")[0].record.first_missing_id is not None


def test_planned_rotation_produces_no_gap(tmp_path: Path) -> None:
    def rotations(run: Run) -> int:
        return sum(s.close_reason == "rotation" for s in run.state.sessions())

    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.flowing()
                await run.eventually(lambda: rotations(run) >= 3, timeout_s=10)

            config = fast_config(tmp_path, market.url, rotation_after_s=0.4)
            return await collect_while(config, FakeRest(), scenario)

    run = asyncio.run(main())
    assert rotations(run) >= 3
    assert run.gaps() == [], [g.record for g in run.gaps()]
    sessions_with_trades = {s for s, _, _ in raw_records(run.raw_dir() / "btcusdt@trade")}
    assert len(sessions_with_trades) >= 4


def test_silent_channel_forces_reconnect_and_marks_book_gap(tmp_path: Path) -> None:
    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.flowing()
                market.paused.add("btcusdt@depth20@100ms")
                await run.eventually(
                    lambda: any(
                        (s.close_reason or "").startswith("silence") for s in run.state.sessions()
                    )
                )
                market.paused.clear()
                await run.eventually(lambda: bool(run.gaps("btcusdt@depth20@100ms")))

            return await collect_while(fast_config(tmp_path, market.url), FakeRest(), scenario)

    run = asyncio.run(main())
    reasons = [s.close_reason or "" for s in run.state.sessions()]
    assert any(r == "silence:btcusdt@depth20@100ms" for r in reasons)
    [gap] = run.gaps("btcusdt@depth20@100ms")
    assert gap.record.cause == "book_stale"
    assert gap.status == "irrecoverable"


def test_restart_detects_gap_since_last_run(tmp_path: Path) -> None:
    async def main() -> Run:
        async with FakeMarket() as market:

            async def first(run: Run) -> None:
                await run.flowing()

            config = fast_config(tmp_path, market.url)
            await collect_while(config, FakeRest(), first)
            market.skip_trades(50)

            async def second(run: Run) -> None:
                await run.eventually(lambda: bool(run.gaps("btcusdt@trade")))

            return await collect_while(config, FakeRest(), second)

    run = asyncio.run(main())
    [gap] = run.gaps("btcusdt@trade")
    assert gap.record.first_missing_id is not None and gap.record.last_missing_id is not None
    assert gap.record.last_missing_id - gap.record.first_missing_id + 1 >= 50
    assert len(run.state.sessions()) == 2


def test_clock_probe_is_a_burst_with_one_offset_per_round(tmp_path: Path) -> None:
    """Doc 06 §4.2: cada medição faz vários pedidos, grava todos na bruta e registra um offset."""

    def offsets(run: Run) -> list[tuple[int, int]]:
        return run.state.clock_offsets_between(0, 2**62)

    async def main() -> Run:
        async with FakeMarket() as market:

            async def scenario(run: Run) -> None:
                await run.eventually(lambda: len(offsets(run)) >= 2)

            config = fast_config(
                tmp_path, market.url, clock_probe_samples=3, clock_probe_interval_s=0.2
            )
            return await collect_while(config, FakeRest(), scenario)

    run = asyncio.run(main())
    rounds = len(offsets(run))
    calls = run.rest.calls["/api/v3/time"]
    assert 3 * rounds <= calls < 3 * (rounds + 1)
    assert len(raw_records(run.raw_dir() / "rest_time")) == calls
