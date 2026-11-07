"""Conferência de continuidade entre sessões e instâncias (RF-160; doc 03 §6.3).

O coletor confere cada sequência dentro da própria execução. Aqui, a conferência junta todas as
sessões de uma hora na camada bruta e compara com o fim da hora anterior: é o que acha um buraco
que nenhuma instância viu, como o de uma troca de versão (doc 06 §9.2). Sem hora anterior, é o
início da coleta: o trecho do primeiro minuto antes do primeiro registro vira lacuna (doc 06 §8.1).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trader.core.timeunits import MINUTE_US
from trader.ops.state import ContinuityMark, GapRecord, StoredGap
from trader.storage.rawfiles import BadLine, iter_records

COLLECTION_START = "collection_start"


@dataclass
class _Sequence:
    """Intervalos de ids vistos: primeiro id → (último id, horário)."""

    spans: dict[int, tuple[int, int]] = field(default_factory=dict)

    def add(self, first: int, last: int, ts_us: int) -> None:
        current = self.spans.get(first)
        if current is None or last > current[0]:
            self.spans[first] = (last, ts_us)


def _payload(line: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(line).get("data")
    except (ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def _collection_start(channel: str, first_ts_us: int, now_us: int) -> list[GapRecord]:
    """Canal sem registro em hora anterior: a coleta começou no meio do minuto do primeiro
    registro, que fica `missing` (doc 06 §8.1). Sem recuperação: o primeiro id que falta é
    desconhecido. Começar no início exato do minuto não deixa lacuna."""
    minute_start = first_ts_us - first_ts_us % MINUTE_US
    if minute_start == first_ts_us:
        return []
    return [
        GapRecord(
            channel=channel,
            start_ts_us=minute_start,
            end_ts_us=first_ts_us,
            cause=COLLECTION_START,
            detected_ts_us=now_us,
            recoverable=False,
        )
    ]


def _id_holes(
    channel: str,
    cause: str,
    recoverable: bool,
    seen: _Sequence,
    previous: ContinuityMark | None,
    now_us: int,
) -> tuple[list[GapRecord], ContinuityMark | None]:
    spans = sorted(seen.spans.items())
    holes = []
    if previous is None and spans:
        holes += _collection_start(channel, spans[0][1][1], now_us)
    mark = previous
    for first, (last, ts_us) in spans:
        if mark is not None and mark.last_id is not None and first > mark.last_id + 1:
            holes.append(
                GapRecord(
                    channel=channel,
                    start_ts_us=mark.last_ts_us,
                    end_ts_us=ts_us,
                    cause=cause,
                    detected_ts_us=now_us,
                    recoverable=recoverable,
                    first_missing_id=mark.last_id + 1,
                    last_missing_id=first - 1,
                )
            )
        if mark is None or mark.last_id is None or last > mark.last_id:
            mark = ContinuityMark(last, ts_us)
    return holes, mark


def check_hour(
    streams: tuple[str, ...],
    paths: Iterable[tuple[str, Path]],
    previous: dict[str, ContinuityMark],
    top20_limit_us: int,
    now_us: int,
) -> tuple[list[GapRecord], dict[str, ContinuityMark]]:
    """Buracos da hora e as marcas do seu fim. `paths`: (canal, arquivo bruto), todas as sessões."""
    trade_stream, _, top20_stream, diff_stream = streams
    trades, diffs = _Sequence(), _Sequence()
    top20: set[int] = set()
    for channel, path in paths:
        for record in iter_records(path):
            if isinstance(record, BadLine):
                continue
            if channel == top20_stream:
                top20.add(record.recv_ts_us)
                continue
            data = _payload(record.payload)
            if data is None:
                continue  # malformada: a normalização manda para a quarentena
            try:
                if channel == trade_stream:
                    trades.add(int(data["t"]), int(data["t"]), int(data["T"]))
                elif channel == diff_stream:
                    diffs.add(int(data["U"]), int(data["u"]), int(data["E"]))
            except (KeyError, TypeError, ValueError):
                continue

    marks = dict(previous)
    gaps: list[GapRecord] = []
    for channel, cause, recoverable, seen in (
        (trade_stream, "trade_id_jump", True, trades),
        (diff_stream, "diff_break", False, diffs),
    ):
        holes, mark = _id_holes(channel, cause, recoverable, seen, previous.get(channel), now_us)
        gaps += holes
        if mark is not None:
            marks[channel] = mark

    last = previous[top20_stream].last_ts_us if top20_stream in previous else None
    snapshots = sorted(top20)
    if last is None and snapshots:
        gaps += _collection_start(top20_stream, snapshots[0], now_us)
    for recv_us in snapshots:
        if last is not None and recv_us - last > top20_limit_us:
            gaps.append(
                GapRecord(
                    channel=top20_stream,
                    start_ts_us=last,
                    end_ts_us=recv_us,
                    cause="book_stale",
                    detected_ts_us=now_us,
                    recoverable=False,
                )
            )
        last = recv_us if last is None else max(last, recv_us)
    if last is not None:
        marks[top20_stream] = ContinuityMark(None, last)
    return gaps, marks


def already_registered(gap: GapRecord, existing: Iterable[StoredGap]) -> bool:
    """Por ids, quando os dois têm; senão, por interseção de horário."""
    for stored in existing:
        other = stored.record
        if other.channel != gap.channel:
            continue
        if (
            gap.first_missing_id is not None
            and gap.last_missing_id is not None
            and other.first_missing_id is not None
            and other.last_missing_id is not None
        ):
            if (
                gap.first_missing_id <= other.last_missing_id
                and other.first_missing_id <= gap.last_missing_id
            ):
                return True
        elif gap.start_ts_us < other.end_ts_us and other.start_ts_us < gap.end_ts_us:
            return True
    return False
