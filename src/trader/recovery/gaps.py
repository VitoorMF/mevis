"""Recuperação de candles pela REST e de negócios pelos arquivos diários (doc 06 §9.1)."""

import csv
import io
import json
import logging
import os
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx

from trader.core.rawformat import RawRecord, encode
from trader.core.timeunits import MINUTE_US, TimestampRangeError, archive_unit, to_us
from trader.ops.config import PipelineConfig
from trader.ops.ids import new_session_id
from trader.ops.logs import ctx
from trader.ops.state import StateStore, StoredGap
from trader.sources.binance_archive import (
    ArchiveClient,
    ArchiveError,
    ArchiveFile,
    ArchiveNotFound,
)
from trader.sources.binance_rest import (
    IpBannedError,
    RateLimitedError,
    RestError,
    get_klines_sync,
)
from trader.storage.layout import (
    HOUR_US,
    RECOVERED_KLINES,
    RECOVERED_TRADES,
    channel_dir,
    hour_dirs,
    raw_root,
)

log = logging.getLogger(__name__)

KLINES_PER_CALL = 1000
DAY_US = 86_400_000_000
_EPOCH = date(1970, 1, 1)


@dataclass
class RecoveryReport:
    recovered: int = 0
    irrecoverable: int = 0
    pending: int = 0
    errors: list[str] = field(default_factory=list)


class GapRecovery:
    def __init__(
        self,
        config: PipelineConfig,
        state: StateStore,
        client: httpx.Client,
        archive: ArchiveClient,
        now_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self._config = config
        self._state = state
        self._client = client
        self._archive = archive
        self._now_us = now_us
        self._symbol = config.symbol
        trade_stream, kline_stream, _, _ = config.streams
        self._trade_stream, self._kline_stream = trade_stream, kline_stream

    def run(self, now_us: int) -> RecoveryReport:
        report = RecoveryReport()
        rest_allowed = True
        for gap in self._state.open_gaps(self._kline_stream):
            # O último minuto ausente precisa ter fechado; senão, fica para a próxima rodada.
            if not rest_allowed or gap.record.end_ts_us + MINUTE_US > now_us:
                report.pending += 1
                continue
            try:
                self._klines(gap, report)
            except (RateLimitedError, IpBannedError) as exc:
                rest_allowed = False  # nenhuma outra chamada REST nesta rodada (RF-145)
                self._failed(gap, exc, report)
            except RestError as exc:
                self._failed(gap, exc, report)
        for gap in self._state.open_gaps(self._trade_stream):
            if gap.record.first_missing_id is None or gap.record.last_missing_id is None:
                report.pending += 1  # sem ids não há como procurar no arquivo
                continue
            try:
                self._trades(gap, now_us, report)
            except ArchiveNotFound:
                report.pending += 1  # arquivo do dia ainda não publicado
            except (ArchiveError, httpx.HTTPError, OSError) as exc:
                self._failed(gap, exc, report)
        return report

    # ------------------------------------------------------------------ candles

    def _klines(self, gap: StoredGap, report: RecoveryReport) -> None:
        start, end = gap.record.start_ts_us, gap.record.end_ts_us
        expected = set(range(start, end, MINUTE_US))
        by_hour: dict[int, list[list[object]]] = defaultdict(list)
        recv_us = 0
        for chunk in range(start, end, KLINES_PER_CALL * MINUTE_US):
            chunk_end = min(end, chunk + KLINES_PER_CALL * MINUTE_US)
            response = get_klines_sync(
                self._client,
                self._config.rest_base_url,
                self._symbol,
                chunk // 1000,
                (chunk_end - 1) // 1000,
            )
            recv_us = response.recv_ts_us
            rows = json.loads(response.body)
            if not isinstance(rows, list):
                raise RestError(f"resposta inesperada de klines: {response.body[:80]!r}")
            for row in rows:
                if isinstance(row, list) and row and row[0] in expected:
                    by_hour[row[0] // HOUR_US].append(row)
        found = {row[0] for rows in by_hour.values() for row in rows}
        session = new_session_id(self._now_us() // 1000)
        for hour, rows in sorted(by_hour.items()):
            payload = json.dumps(rows, separators=(",", ":")).encode()
            _write_raw(self._config, RECOVERED_KLINES, hour, session, [(recv_us, payload)])
        complete = expected <= found
        self._close(gap, complete, RECOVERED_KLINES, report, found=len(found & expected))

    # ------------------------------------------------------------------ negócios

    def _trades(self, gap: StoredGap, now_us: int, report: RecoveryReport) -> None:
        first, last = gap.record.first_missing_id, gap.record.last_missing_id
        assert first is not None and last is not None
        days = _days(gap.record.start_ts_us, gap.record.end_ts_us)
        if _day_start_us(days[-1] + timedelta(days=1)) > now_us:
            report.pending += 1  # o dia ainda não terminou: o arquivo não existe
            return
        found: dict[int, tuple[ArchiveFile, str, list[str]]] = {}
        origins = []
        for day in days:
            archive = ArchiveFile("trades", self._symbol, False, day, day)
            expected_sha = self._archive.checksum(archive)
            path = self._config.data_dir / "tmp" / "recovery" / archive.name
            try:
                actual = self._archive.download(archive, path)
                if actual != expected_sha:
                    raise ArchiveError(f"checksum divergente em {archive.name}")
                for fields in _rows_between(path, first, last):
                    found[int(fields[0])] = (archive, actual, fields)
            finally:
                path.unlink(missing_ok=True)
            origins.append(f"{archive.name}@{actual}")
        session = new_session_id(self._now_us() // 1000)
        recv_us = self._now_us()
        by_hour: dict[int, list[tuple[int, bytes]]] = defaultdict(list)
        for trade_id in sorted(found):
            archive, sha, fields = found[trade_id]
            unit = archive_unit(archive.first_day)
            payload = {"file": archive.name, "sha256": sha, "unit": unit.value, "row": fields}
            try:
                hour = to_us(int(fields[4]), unit) // HOUR_US
            except (ValueError, TimestampRangeError):
                hour = gap.record.start_ts_us // HOUR_US  # a normalização vai recusar a linha
            line = json.dumps(payload, separators=(",", ":")).encode()
            by_hour[hour].append((recv_us, line))
        for hour, records in sorted(by_hour.items()):
            _write_raw(self._config, RECOVERED_TRADES, hour, session, records)
        complete = len(found) == last - first + 1
        origin = "archive:" + ",".join(origins)
        self._close(gap, complete, origin, report, found=len(found))

    # ------------------------------------------------------------------ apoio

    def _close(
        self, gap: StoredGap, complete: bool, origin: str, report: RecoveryReport, found: int
    ) -> None:
        status = "recovered" if complete else "irrecoverable"
        self._state.set_gap_status(gap.gap_id, status, origin)
        if complete:
            report.recovered += 1
        else:
            report.irrecoverable += 1
        log.info(
            "lacuna recuperada" if complete else "lacuna irrecuperável",
            extra=ctx(gap_id=gap.gap_id, channel=gap.record.channel, found=found, origin=origin),
        )

    def _failed(self, gap: StoredGap, exc: Exception, report: RecoveryReport) -> None:
        report.pending += 1
        report.errors.append(f"lacuna {gap.gap_id}: {exc}")
        log.warning("recuperação adiada", extra=ctx(gap_id=gap.gap_id, error=repr(exc)))


def _rows_between(zip_path: Path, first: int, last: int) -> Iterator[list[str]]:
    """Linhas do CSV com id em `[first, last]`; o arquivo vem ordenado por id."""
    with zipfile.ZipFile(zip_path) as bundle:
        members = [n for n in bundle.namelist() if n.endswith(".csv")]
        if len(members) != 1:
            raise ArchiveError(f"{zip_path.name}: esperado 1 CSV, há {members}")
        with bundle.open(members[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="ascii", errors="replace", newline="")
            for fields in csv.reader(text):
                if not fields or not fields[0].isdigit():
                    continue  # cabeçalho
                trade_id = int(fields[0])
                if trade_id > last:
                    return
                if trade_id >= first:
                    yield fields


def _write_raw(
    config: PipelineConfig,
    channel: str,
    hour: int,
    session: str,
    records: list[tuple[int, bytes]],
) -> Path:
    """Grava um arquivo bruto já fechado (`.jsonl`), que o pipeline comprime em seguida."""
    date_dir, hour_dir = hour_dirs(hour)
    directory = raw_root(config.data_dir, config.symbol) / channel_dir(channel) / date_dir
    path = directory / hour_dir / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as out:
        for seq, (recv_us, payload) in enumerate(records, 1):
            out.write(encode(RawRecord(recv_us, session, seq, channel, payload)))
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    return path


def _days(start_us: int, end_us: int) -> list[date]:
    first = _EPOCH + timedelta(days=start_us // DAY_US)
    last = _EPOCH + timedelta(days=end_us // DAY_US)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _day_start_us(day: date) -> int:
    return (day - _EPOCH).days * DAY_US
