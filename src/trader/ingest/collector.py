"""Coletor contínuo (doc 03 §4.1, §6.1-6.2; SRS §4.1-4.4).

Cada sessão é uma conexão WebSocket com todos os streams. Toda mensagem recebida, de qualquer
sessão, vai para a camada bruta. As verificações de continuidade (ids de negócio, diferencial do
livro, minutos de candle, intervalo do top 20) usam só a sessão **primária**.

Rotação planejada (RF-115): uma sessão nova é aberta em paralelo; enquanto "aquece", suas
mensagens são gravadas e guardadas num buffer. Quando ela recebe todos os streams e passa o
período de sobreposição, o buffer é reprocessado (duplicatas viram REPEATED nos rastreadores),
ela vira primária e a antiga é fechada. Nenhuma mensagem fica de fora das verificações.
"""

import asyncio
import contextlib
import json
import logging
import random
from collections import defaultdict, deque
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any

from trader.core.rawformat import RawRecord, is_valid_token
from trader.core.timeunits import MINUTE_US, SECOND_US, TimeUnit
from trader.ingest.clockprobe import measure_offset
from trader.ingest.raw_writer import RawWriter, StorageError
from trader.normalize.binance import (
    NormalizationError,
    parse_ws_depth_diff_header,
    parse_ws_kline,
    parse_ws_trade,
)
from trader.ops.clock import Clock
from trader.ops.config import CollectorConfig
from trader.ops.health import health_path, write_health
from trader.ops.ids import new_session_id
from trader.ops.logs import ctx
from trader.ops.metrics import Metrics
from trader.ops.state import GapRecord, StateStore
from trader.quality.sequences import (
    DiffContinuityTracker,
    MinuteSequenceTracker,
    SeqStatus,
    SilenceMonitor,
    TradeIdTracker,
)
from trader.sources.binance_rest import BinanceRest, RestError, RestResponse
from trader.sources.binance_ws import Connector, WsConnection, stream_url

log = logging.getLogger(__name__)

UNIT = TimeUnit.MICROSECOND
CONTROL_CHANNEL = "ws:control"
UNPARSED_CHANNEL = "ws:unparsed"
CONNECTION_WINDOW_US = 300 * SECOND_US
CONNECTION_ATTEMPT_LIMIT = 250  # abaixo do limite da fonte: 300 a cada 5 min por IP (FV-02)
ROTATION_RETRY_US = 60 * SECOND_US

_Buffered = tuple[str, Mapping[str, Any], int, int]


@dataclass(eq=False)
class _Session:
    session_id: str
    endpoint: str
    conn: WsConnection
    rotate_at_mono_us: int
    seq: int = 0
    messages: int = 0
    channels_seen: set[str] = field(default_factory=set)
    warming: bool = False
    buffer: deque[_Buffered] = field(default_factory=lambda: deque(maxlen=200_000))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    close_reason: str | None = None
    closed: bool = False


@dataclass
class _ChannelStats:
    messages: int = 0
    last_recv_us: int | None = None


def classify(data: bytes) -> tuple[str, Mapping[str, Any] | None]:
    """Canal e conteúdo (`data`) da mensagem; fora do formato, vai para um canal próprio."""
    try:
        message = json.loads(data)
    except ValueError:
        return UNPARSED_CHANNEL, None
    if isinstance(message, dict):
        stream, payload = message.get("stream"), message.get("data")
        if isinstance(stream, str) and isinstance(payload, dict) and is_valid_token(stream):
            return stream, payload
    return CONTROL_CHANNEL, None


class Collector:
    def __init__(
        self,
        config: CollectorConfig,
        *,
        clock: Clock,
        writer: RawWriter,
        state: StateStore,
        rest: BinanceRest,
        metrics: Metrics,
        connector: Connector,
        code_version: str,
    ) -> None:
        self._config = config
        self._clock = clock
        self._writer = writer
        self._state = state
        self._rest = rest
        self._metrics = metrics
        self._connector = connector
        self._code_version = code_version

        self._streams = config.streams
        self._trade_stream, self._kline_stream, self._top20_stream, self._diff_stream = (
            self._streams
        )
        self._limits_us = {s: int(config.silence_limit_s(s) * SECOND_US) for s in self._streams}

        self._primary: _Session | None = None
        self._sessions: list[_Session] = []
        self._silence = SilenceMonitor(self._limits_us, clock.mono_us())
        self._snapshot_requested = asyncio.Event()
        self._fatal: BaseException | None = None
        self._connect_attempts: deque[int] = deque()

        self._trade_ids = TradeIdTracker()
        self._diff = DiffContinuityTracker()
        self._minutes = MinuteSequenceTracker()
        self._last_trade_ts_us: int | None = None
        self._last_diff_ts_us: int | None = None
        self._last_top20_recv_us: int | None = None
        self._load_watermarks()

        self._rest_session = new_session_id(clock.now_us() // 1000)
        self._rest_seq = 0
        self._stats: defaultdict[str, _ChannelStats] = defaultdict(_ChannelStats)
        self._gaps_total = 0
        self._gaps_since_metrics = 0
        self._reconnects = 0
        self._malformed = 0
        self._messages_since_metrics = 0
        self._last_offset_us: int | None = None
        self._started_us = clock.now_us()

    # ------------------------------------------------------------------ ciclo de vida

    async def run(self, stop: asyncio.Event) -> None:
        loops = [
            self._guard(self._flush_loop(stop)),
            self._guard(self._health_loop(stop)),
            self._guard(self._metrics_loop(stop)),
            self._guard(self._clock_loop(stop)),
            self._guard(self._snapshot_loop(stop)),
        ]
        background = [asyncio.create_task(loop) for loop in loops]
        log.info("coletor iniciado", extra=ctx(streams=list(self._streams)))
        try:
            await self._write_rest(self._rest.exchange_info(self._config.symbol))
            await self._supervise(stop)
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            for session in list(self._sessions):
                await self._close_session(session, "shutdown")
            self._save_watermarks()
            self._writer.close()
            self._write_health("stopped")
            log.info("coletor parado")

    async def _supervise(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            if self._fatal is not None:
                raise self._fatal
            primary = self._primary
            if primary is None:
                session = await self._open_session()
                if session is None:
                    failures += 1
                    await self._sleep(stop, self._backoff_s(failures))
                    continue
                failures = 0
                self._promote(session)
                continue
            assert primary.task is not None
            if primary.task.done():
                await self._lose_primary(primary, primary.close_reason or "ended")
                continue
            now = self._clock.mono_us()
            silent = self._silence.silent(now)
            if silent:
                await self._lose_primary(primary, "silence:" + ",".join(silent))
                continue
            if now >= primary.rotate_at_mono_us:
                await self._rotate(primary, stop)
                continue
            await self._wait_any(stop, primary.task, timeout_s=1.0)
        if self._fatal is not None:
            raise self._fatal

    async def _lose_primary(self, session: _Session, reason: str) -> None:
        log.warning("sessão primária perdida", extra=ctx(session=session.session_id, reason=reason))
        await self._close_session(session, reason)
        self._primary = None
        self._reconnects += 1

    def _promote(self, session: _Session) -> None:
        """Torna a sessão primária, reprocessando o que ela recebeu enquanto aquecia."""
        self._primary = session
        session.warming = False
        for item in session.buffer:
            self._process(*item)
        session.buffer.clear()
        self._silence = SilenceMonitor(self._limits_us, self._clock.mono_us())
        self._snapshot_requested.set()
        log.info(
            "sessão primária", extra=ctx(session=session.session_id, endpoint=session.endpoint)
        )

    async def _rotate(self, old: _Session, stop: asyncio.Event) -> None:
        new = await self._open_session()
        if new is None:
            old.rotate_at_mono_us = self._clock.mono_us() + ROTATION_RETRY_US
            return
        new.warming = True
        assert new.task is not None and old.task is not None
        ready = asyncio.create_task(new.ready.wait())
        try:
            await self._wait_any(
                stop, ready, new.task, old.task, timeout_s=self._config.handover_timeout_s
            )
        finally:
            ready.cancel()
        if stop.is_set():
            return
        old_alive = not old.task.done()
        if old_alive and new.ready.is_set() and not new.task.done():
            # Sobreposição: garante que a sessão antiga já processou o que a nova tem no buffer.
            await self._wait_any(stop, new.task, timeout_s=self._config.handover_overlap_s)
            if stop.is_set():
                return
        if new.task.done() or (old_alive and not new.ready.is_set()):
            log.warning("rotação adiada", extra=ctx(session=new.session_id))
            await self._close_session(new, new.close_reason or "handover_timeout")
            old.rotate_at_mono_us = self._clock.mono_us() + ROTATION_RETRY_US
            return
        # Nova sessão pronta, ou a antiga morreu durante o aquecimento: a nova assume.
        self._promote(new)
        reason = "rotation" if not old.task.done() else (old.close_reason or "ended")
        await self._close_session(old, reason)

    # ------------------------------------------------------------------ sessões

    async def _open_session(self) -> _Session | None:
        for endpoint in self._config.ws_base_urls:
            await self._respect_connection_limit()
            url = stream_url(endpoint, self._streams)
            try:
                conn = await asyncio.wait_for(
                    self._connector(url), timeout=self._config.open_timeout_s
                )
            except Exception as exc:
                log.warning("falha ao conectar", extra=ctx(endpoint=endpoint, error=repr(exc)))
                continue
            now_us, mono_us = self._clock.now_us(), self._clock.mono_us()
            session = _Session(
                session_id=new_session_id(now_us // 1000),
                endpoint=endpoint,
                conn=conn,
                rotate_at_mono_us=mono_us + int(self._config.rotation_after_s * SECOND_US),
            )
            self._state.session_opened(
                session.session_id, endpoint, self._streams, now_us, self._code_version
            )
            session.task = asyncio.create_task(self._read(session))
            self._sessions.append(session)
            log.info("sessão aberta", extra=ctx(session=session.session_id, endpoint=endpoint))
            return session
        return None

    async def _close_session(self, session: _Session, reason: str) -> None:
        if session.closed:
            return
        session.closed = True
        if session.task is not None and not session.task.done():
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.conn.close(), timeout=5)
        self._writer.seal_session(session.session_id)
        self._state.session_closed(
            session.session_id, self._clock.now_us(), reason, session.messages
        )
        self._sessions.remove(session)
        log.info("sessão fechada", extra=ctx(session=session.session_id, reason=reason))

    async def _read(self, session: _Session) -> None:
        try:
            while True:
                data = await session.conn.recv()
                self._on_message(session, data, self._clock.now_us(), self._clock.mono_us())
        except asyncio.CancelledError:
            raise
        except StorageError as exc:
            self._fatal = exc
            session.close_reason = f"storage:{exc}"
        except Exception as exc:
            session.close_reason = f"closed:{exc!r}"

    async def _respect_connection_limit(self) -> None:
        now = self._clock.mono_us()
        while self._connect_attempts and now - self._connect_attempts[0] >= CONNECTION_WINDOW_US:
            self._connect_attempts.popleft()
        if len(self._connect_attempts) >= CONNECTION_ATTEMPT_LIMIT:
            wait_us = self._connect_attempts[0] + CONNECTION_WINDOW_US - now
            log.warning("limite de conexões por IP; aguardando", extra=ctx(wait_s=wait_us / 1e6))
            await asyncio.sleep(wait_us / 1e6)
        self._connect_attempts.append(self._clock.mono_us())

    def _backoff_s(self, failures: int) -> float:
        delay = min(
            self._config.backoff_max_s, self._config.backoff_initial_s * 2.0 ** (failures - 1)
        )
        return delay / 2 + random.uniform(0, delay / 2)

    # ------------------------------------------------------------------ mensagens

    def _on_message(self, session: _Session, data: bytes, recv_us: int, mono_us: int) -> None:
        channel, payload = classify(data)
        session.seq += 1
        self._writer.write(RawRecord(recv_us, session.session_id, session.seq, channel, data))
        session.messages += 1
        if payload is None:
            if channel == UNPARSED_CHANNEL:
                self._malformed += 1
            return
        session.channels_seen.add(channel)
        if session is self._primary:
            self._process(channel, payload, recv_us, mono_us)
        elif session.warming:
            session.buffer.append((channel, payload, recv_us, mono_us))
            if not session.ready.is_set() and session.channels_seen.issuperset(self._streams):
                session.ready.set()

    def _process(self, channel: str, data: Mapping[str, Any], recv_us: int, mono_us: int) -> None:
        self._silence.touch(channel, mono_us)
        stats = self._stats[channel]
        stats.messages += 1
        stats.last_recv_us = recv_us
        self._messages_since_metrics += 1
        try:
            if channel == self._trade_stream:
                self._check_trade(data, recv_us)
            elif channel == self._kline_stream:
                self._check_kline(data, recv_us)
            elif channel == self._top20_stream:
                self._check_top20(recv_us)
            elif channel == self._diff_stream:
                self._check_diff(data, recv_us)
        except NormalizationError as exc:
            self._malformed += 1
            log.warning("mensagem inválida", extra=ctx(channel=channel, error=str(exc)))

    def _check_trade(self, data: Mapping[str, Any], recv_us: int) -> None:
        trade = parse_ws_trade(data, recv_ts_us=recv_us, unit=UNIT, provenance="live-check")
        result = self._trade_ids.observe(trade.trade_id)
        if result.gap is not None:
            self._record_gap(
                GapRecord(
                    channel=self._trade_stream,
                    start_ts_us=self._last_trade_ts_us or trade.trade_ts_us,
                    end_ts_us=trade.trade_ts_us,
                    cause="trade_id_jump",
                    detected_ts_us=recv_us,
                    recoverable=True,
                    first_missing_id=result.gap.first_missing,
                    last_missing_id=result.gap.last_missing,
                )
            )
        if result.status is not SeqStatus.REPEATED:
            self._last_trade_ts_us = trade.trade_ts_us

    def _check_kline(self, data: Mapping[str, Any], recv_us: int) -> None:
        kline = parse_ws_kline(data, recv_ts_us=recv_us, unit=UNIT, provenance="live-check")
        if kline is None:
            return
        missing = self._minutes.observe(kline.open_ts_us)
        if missing:
            self._record_gap(
                GapRecord(
                    channel=self._kline_stream,
                    start_ts_us=missing[0],
                    end_ts_us=missing[-1] + MINUTE_US,
                    cause="kline_missing",
                    detected_ts_us=recv_us,
                    recoverable=True,
                )
            )

    def _check_top20(self, recv_us: int) -> None:
        previous = self._last_top20_recv_us
        if previous is not None and recv_us - previous > self._limits_us[self._top20_stream]:
            self._record_gap(
                GapRecord(
                    channel=self._top20_stream,
                    start_ts_us=previous,
                    end_ts_us=recv_us,
                    cause="book_stale",
                    detected_ts_us=recv_us,
                    recoverable=False,
                )
            )
        self._last_top20_recv_us = max(previous or recv_us, recv_us)

    def _check_diff(self, data: Mapping[str, Any], recv_us: int) -> None:
        header = parse_ws_depth_diff_header(data, unit=UNIT)
        result = self._diff.observe(header.first_update_id, header.final_update_id)
        if result.gap is not None:
            self._record_gap(
                GapRecord(
                    channel=self._diff_stream,
                    start_ts_us=self._last_diff_ts_us or header.event_ts_us,
                    end_ts_us=header.event_ts_us,
                    cause="diff_break",
                    detected_ts_us=recv_us,
                    recoverable=False,
                    first_missing_id=result.gap.first_missing,
                    last_missing_id=result.gap.last_missing,
                )
            )
            self._snapshot_requested.set()
        if result.status is not SeqStatus.REPEATED:
            self._last_diff_ts_us = header.event_ts_us

    def _record_gap(self, gap: GapRecord) -> None:
        self._state.record_gap(gap)
        self._gaps_total += 1
        self._gaps_since_metrics += 1
        log.warning(
            "lacuna detectada",
            extra=ctx(
                channel=gap.channel,
                cause=gap.cause,
                duration_s=(gap.end_ts_us - gap.start_ts_us) / 1e6,
                first_missing_id=gap.first_missing_id,
                last_missing_id=gap.last_missing_id,
                recoverable=gap.recoverable,
            ),
        )

    # ------------------------------------------------------------------ REST

    async def _write_rest(self, call: Coroutine[Any, Any, RestResponse]) -> RestResponse | None:
        try:
            response = await call
        except RestError as exc:
            log.warning("falha REST", extra=ctx(error=str(exc)))
            return None
        body = response.body.rstrip(b"\r\n")
        if b"\n" in body:
            log.error(
                "resposta REST com quebra de linha; descartada", extra=ctx(channel=response.channel)
            )
            return None
        self._rest_seq += 1
        self._writer.write(
            RawRecord(
                response.recv_ts_us, self._rest_session, self._rest_seq, response.channel, body
            )
        )
        return response

    async def _snapshot_loop(self, stop: asyncio.Event) -> None:
        """Snapshot completo do livro: ao virar primária, a cada 15 min e após quebra (RF-193)."""
        last_mono: int | None = None
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._snapshot_requested.wait(), timeout=self._config.snapshot_interval_s
                )
            if stop.is_set():
                return
            self._snapshot_requested.clear()
            if self._primary is None:
                continue
            if last_mono is not None:
                elapsed_s = (self._clock.mono_us() - last_mono) / 1e6
                await self._sleep(stop, self._config.snapshot_min_interval_s - elapsed_s)
            last_mono = self._clock.mono_us()
            await self._write_rest(
                self._rest.depth(self._config.symbol, self._config.snapshot_limit)
            )

    async def _clock_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            response = await self._write_rest(self._rest.server_time())
            if response is not None:
                try:
                    offset = measure_offset(response)
                except (ValueError, KeyError) as exc:
                    log.warning("resposta de horário inválida", extra=ctx(error=repr(exc)))
                else:
                    self._state.record_clock_offset(
                        offset.local_ts_us, offset.server_ts_us, offset.rtt_us, offset.offset_us
                    )
                    self._last_offset_us = offset.offset_us
                    level = (
                        logging.WARNING
                        if abs(offset.offset_us) / 1000 > self._config.clock_offset_alert_ms
                        else logging.INFO
                    )
                    log.log(
                        level,
                        "offset de relógio",
                        extra=ctx(offset_ms=offset.offset_us / 1000, rtt_ms=offset.rtt_us / 1000),
                    )
            await self._sleep(stop, self._config.clock_probe_interval_s)

    # ------------------------------------------------------------------ rotinas

    async def _flush_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._writer.seal_stale(self._clock.now_us())
            self._writer.flush()
            await self._sleep(stop, self._config.flush_interval_s)

    async def _health_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._write_health("running")
            self._save_watermarks()
            await self._sleep(stop, self._config.health_interval_s)

    async def _metrics_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._sleep(stop, self._config.metrics_interval_s)
            now = self._clock.now_us()
            ages = [
                (now - s.last_recv_us) / 1e6 if s.last_recv_us else float("inf")
                for s in (self._stats[stream] for stream in self._streams)
            ]
            self._metrics.gauge("Heartbeat", 1)
            self._metrics.gauge("MaxChannelAgeSeconds", min(max(ages), 1e6))
            # Contagens por intervalo: o alarme soma os pontos (doc 03 §7.5).
            self._metrics.gauge("GapsDetected", self._gaps_since_metrics)
            self._metrics.gauge("MessagesPerMinute", self._messages_since_metrics)
            if self._last_offset_us is not None:
                self._metrics.gauge("ClockOffsetMs", abs(self._last_offset_us) / 1000)
            self._messages_since_metrics = 0
            self._gaps_since_metrics = 0

    async def _guard(self, loop: Coroutine[Any, Any, None]) -> None:
        """Erro inesperado em rotina de fundo é fatal: melhor reiniciar que gravar sem fsync."""
        try:
            await loop
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            log.exception("rotina de fundo falhou")
            self._fatal = exc

    # ------------------------------------------------------------------ estado

    def _load_watermarks(self) -> None:
        marks = self._state.load_watermarks()
        symbol = self._config.symbol
        trade_id, self._last_trade_ts_us = marks.get(f"{symbol}:trade", (None, None))
        diff_id, self._last_diff_ts_us = marks.get(f"{symbol}:diff", (None, None))
        _, kline_open = marks.get(f"{symbol}:kline", (None, None))
        _, self._last_top20_recv_us = marks.get(f"{symbol}:top20", (None, None))
        self._trade_ids = TradeIdTracker(trade_id)
        self._diff = DiffContinuityTracker(diff_id)
        self._minutes = MinuteSequenceTracker(kline_open)

    def _save_watermarks(self) -> None:
        symbol = self._config.symbol
        self._state.save_watermarks(
            {
                f"{symbol}:trade": (self._trade_ids.last_id, self._last_trade_ts_us),
                f"{symbol}:diff": (self._diff.last_final_id, self._last_diff_ts_us),
                f"{symbol}:kline": (None, self._minutes.last_open_ts_us),
                f"{symbol}:top20": (None, self._last_top20_recv_us),
            }
        )

    def _write_health(self, status: str) -> None:
        now = self._clock.now_us()
        primary = self._primary
        write_health(
            health_path(self._config.data_dir, self._config.instance),
            {
                "status": status,
                "instance": self._config.instance,
                "version": self._code_version,
                "updated_ts_us": now,
                "started_ts_us": self._started_us,
                "primary_session": primary.session_id if primary else None,
                "primary_endpoint": primary.endpoint if primary else None,
                "open_sessions": [s.session_id for s in self._sessions],
                "channels": {
                    stream: {
                        "messages": self._stats[stream].messages,
                        "last_recv_ts_us": self._stats[stream].last_recv_us,
                    }
                    for stream in self._streams
                },
                "gaps_detected": self._gaps_total,
                "reconnects": self._reconnects,
                "malformed_messages": self._malformed,
                "clock_offset_us": self._last_offset_us,
            },
        )

    # ------------------------------------------------------------------ utilitários

    @staticmethod
    async def _sleep(stop: asyncio.Event, seconds: float) -> None:
        if seconds <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)

    @staticmethod
    async def _wait_any(stop: asyncio.Event, *tasks: "asyncio.Task[Any]", timeout_s: float) -> None:
        stopper = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait(
                {stopper, *tasks}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            stopper.cancel()
