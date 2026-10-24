"""Relatório de qualidade de um período (doc 06 §8.3; RF-158, CA-05).

Lê o diretório de dados local: máscara de qualidade, camada normalizada e estado. Não altera
nada. Horas sem máscara (não normalizadas ou fora do disco) aparecem como tal, não como falha.
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.ops.state import StateStore
from trader.storage.layout import HOUR_US, dataset_path

PERCENTILES = (50.0, 90.0, 99.0, 99.9)


@dataclass
class DelayStats:
    count: int = 0
    negative: int = 0
    max_ms: int | None = None
    percentiles_ms: dict[str, int] = field(default_factory=dict)


@dataclass
class QualityReport:
    start: date
    end: date
    hours: int
    hours_without_mask: list[str]
    mask: dict[str, dict[str, int]]
    """Canal → estado → minutos."""
    gaps: dict[str, dict[str, Any]]
    """`canal status` → quantidade e segundos dentro do período."""
    rows: dict[str, int]
    duplicates: dict[str, int]
    quarantined: dict[str, int]
    delays: dict[str, DelayStats]
    clock: dict[str, Any]
    sessions: dict[str, int]
    storage: dict[str, dict[str, int]]
    """Dia → bytes comprimidos e originais da camada bruta."""

    def completeness(self, channel: str) -> float | None:
        statuses = self.mask.get(channel, {})
        total = sum(statuses.values())
        return None if total == 0 else 1 - statuses.get("missing", 0) / total

    def lines(self) -> list[str]:
        out = [
            f"período: {self.start} a {self.end} UTC ({self.hours} h)",
            f"horas sem máscara: {len(self.hours_without_mask)}",
            "",
            "completude (minutos sem `missing`):",
        ]
        for channel, statuses in sorted(self.mask.items()):
            ratio = self.completeness(channel)
            detail = ", ".join(f"{k} {v}" for k, v in sorted(statuses.items()))
            share = "—" if ratio is None else f"{100 * ratio:.3f}%"
            out.append(f"  {channel}: {share} ({detail})")
        out.append("lacunas:" if self.gaps else "lacunas: nenhuma")
        for name, info in sorted(self.gaps.items()):
            out.append(f"  {name}: {info['count']} ({info['seconds']:.1f} s no período)")
        out.append("linhas: " + _join(self.rows))
        out.append("repetições descartadas: " + _join(self.duplicates))
        out.append("quarentena: " + _join(self.quarantined))
        out.append("atraso do evento ao recebimento (ms, sem correção de offset):")
        for name, stats in sorted(self.delays.items()):
            if stats.count == 0:
                out.append(f"  {name}: sem dados ao vivo")
                continue
            pct = " · ".join(f"p{k} {v}" for k, v in stats.percentiles_ms.items())
            out.append(
                f"  {name}: {stats.count} · {pct} · máx. {stats.max_ms}"
                f" · negativos {stats.negative}"
            )
        clock = self.clock
        if clock["samples"]:
            out.append(
                f"relógio: {clock['samples']} medições · offset mediano {clock['median_ms']:+.1f}"
                f" ms · maior em módulo {clock['max_abs_ms']:.1f} ms"
                f" · ida e volta mediana {clock['median_rtt_ms']:.0f} ms"
            )
        else:
            out.append("relógio: sem medições")
        out.append("sessões abertas no período: " + _join(self.sessions))
        out.append("armazenamento bruto por dia (comprimido / original):")
        for day, sizes in sorted(self.storage.items()):
            out.append(
                f"  {day}: {sizes['compressed'] / 1e6:.1f} MB / {sizes['original'] / 1e6:.1f} MB"
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "hours": self.hours,
            "hours_without_mask": self.hours_without_mask,
            "completeness": {c: self.completeness(c) for c in sorted(self.mask)},
            "mask": self.mask,
            "gaps": self.gaps,
            "rows": self.rows,
            "duplicates": self.duplicates,
            "quarantined": self.quarantined,
            "delays": {k: vars(v) for k, v in self.delays.items()},
            "clock": self.clock,
            "sessions": self.sessions,
            "storage": self.storage,
        }


def build_report(
    data_dir: Path, state: StateStore, symbol: str, start: date, end: date
) -> QualityReport:
    if start > end:
        raise ValueError(f"início {start} depois do fim {end}")
    start_us = _day_us(start)
    end_us = _day_us(end + timedelta(days=1))
    first_hour, last_hour = start_us // HOUR_US, end_us // HOUR_US
    hours = range(first_hour, last_hour)

    mask: dict[str, Counter[str]] = {}
    without_mask = []
    trade_delays: Counter[int] = Counter()
    kline_delays: Counter[int] = Counter()
    for hour in hours:
        path = dataset_path(data_dir, "derived", "quality_mask", symbol, hour)
        if not path.exists():
            without_mask.append(_hour_label(hour))
        else:
            table = pq.read_table(path, columns=["channel", "status"])
            for channel, status in zip(
                table.column("channel").to_pylist(),
                table.column("status").to_pylist(),
                strict=True,
            ):
                mask.setdefault(channel, Counter())[status] += 1
        _delays(
            dataset_path(data_dir, "normalized", "trades", symbol, hour),
            "trade_ts_us",
            trade_delays,
        )
        _delays(
            dataset_path(data_dir, "normalized", "klines_1m", symbol, hour),
            "close_ts_us",
            kline_delays,
        )

    gaps: dict[str, dict[str, Any]] = {}
    for gap in state.gaps_overlapping(start_us, end_us):
        record = gap.record
        info = gaps.setdefault(f"{record.channel} {gap.status}", {"count": 0, "seconds": 0.0})
        info["count"] += 1
        overlap = min(record.end_ts_us, end_us) - max(record.start_ts_us, start_us)
        info["seconds"] += overlap / 1e6

    rows: Counter[str] = Counter()
    duplicates: Counter[str] = Counter()
    quarantined: Counter[str] = Counter()
    targets = {"rows": rows, "duplicates": duplicates, "quarantined": quarantined}
    for _, metric, key, value in state.hour_stats_between(symbol, first_hour, last_hour):
        if metric in targets:
            targets[metric][key] += value

    samples = state.clock_samples_between(start_us, end_us)
    clock: dict[str, Any] = {"samples": len(samples)}
    if samples:
        offsets = [offset / 1000 for _, offset, _ in samples]
        clock |= {
            "median_ms": statistics.median(offsets),
            "max_abs_ms": max(abs(o) for o in offsets),
            "median_rtt_ms": statistics.median(rtt / 1000 for _, _, rtt in samples),
        }

    sessions: Counter[str] = Counter()
    for session in state.sessions():
        if start_us <= session.opened_ts_us < end_us:
            sessions[session.close_reason or "aberta"] += 1

    storage: dict[str, dict[str, int]] = {}
    for raw in state.raw_files("hour >= ? AND hour < ?", (first_hour, last_hour)):
        day = _hour_label(raw.hour)[:10]
        sizes = storage.setdefault(day, {"compressed": 0, "original": 0})
        sizes["compressed"] += raw.compressed_size
        sizes["original"] += raw.original_size

    return QualityReport(
        start=start,
        end=end,
        hours=len(hours),
        hours_without_mask=without_mask,
        mask={c: dict(s) for c, s in mask.items()},
        gaps=gaps,
        rows=dict(rows),
        duplicates=dict(duplicates),
        quarantined=dict(quarantined),
        delays={"trades": _summary(trade_delays), "klines_1m": _summary(kline_delays)},
        clock=clock,
        sessions=dict(sessions),
        storage=storage,
    )


def _delays(path: Path, event_column: str, histogram: Counter[int]) -> None:
    """Acumula atrasos em ms (recebimento - evento) dos registros ao vivo do arquivo."""
    if not path.exists():
        return
    table = pq.read_table(path, columns=["recv_ts_us", event_column])
    table = table.filter(pc.is_valid(table.column("recv_ts_us")))
    if table.num_rows == 0:
        return
    delay_us = pc.subtract(table.column("recv_ts_us"), table.column(event_column))
    delay_ms = pc.floor(pc.divide(pc.cast(delay_us, "float64"), 1000.0))
    counts = pc.value_counts(pc.cast(delay_ms, "int64"))
    for item in counts.to_pylist():
        histogram[int(item["values"])] += int(item["counts"])


def _summary(histogram: Counter[int]) -> DelayStats:
    total = sum(histogram.values())
    if total == 0:
        return DelayStats()
    ordered = sorted(histogram.items())
    percentiles = {}
    for target in PERCENTILES:
        rank = target / 100 * total
        cumulative = 0
        for value, count in ordered:
            cumulative += count
            if cumulative >= rank:
                percentiles[f"{target:g}"] = value
                break
    return DelayStats(
        count=total,
        negative=sum(c for v, c in ordered if v < 0),
        max_ms=ordered[-1][0],
        percentiles_ms=percentiles,
    )


def _join(counts: dict[str, int]) -> str:
    return " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nenhuma"


def _day_us(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 1_000_000


def _hour_label(hour: int) -> str:
    return datetime.fromtimestamp(hour * HOUR_US / 1e6, UTC).strftime("%Y-%m-%d %H:00")
