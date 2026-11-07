"""Página pública de status (doc 03 §7.7, ADR-13; regras em doc 06 §8.5).

Publica só métricas do sistema de coleta: estado do coletor, mensagens por canal, lacunas,
relógio e armazenamento. Nunca preços, volumes, quantidades, livro ou negócios individuais, nem
identificadores da infraestrutura (conta, bucket, instância, IP). O histórico cobre só a coleta ao
vivo (arquivos horários), o que deixa o holdout da trilha A de fora (doc 04 §3.3).
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.ops.config import PipelineConfig
from trader.ops.health import health_files, read_health
from trader.pipeline.dashboard_data import (
    CHANNEL_NAMES,
    MINUTE_US,
    collection_start,
    history_counts,
    hour_of,
    hourly_files,
)
from trader.storage.layout import HOUR_US

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SECOND_US = 1_000_000
STALE_HEALTH_US = 30 * SECOND_US
"""Arquivo de saúde mais velho que isso: coletor sem sinal (o mesmo limite de `trader status`)."""
SERIES_WINDOW_S = 15 * 60
HISTORY_HOURS = 7 * 24
HISTORY_CHECK_S = 300.0
TOTALS_CHECK_S = 6 * 3600.0
"""Tamanho do bucket e contagens do backfill: listar o bucket inteiro custa pedidos LIST."""
GAPS_WINDOW_US = 24 * 3600 * SECOND_US
GAPS_LISTED = 20
PLANNED_CLOSES = ("shutdown", "rotation")
"""Fechamentos de sessão previstos (implantação, rotação de 23 h); o resto é reconexão."""
CAUSES = {
    "trade_id_jump": "salto na sequência de negócios",
    "kline_missing": "candle não recebido",
    "book_stale": "livro sem atualização",
    "diff_break": "quebra na sequência do livro",
    "collection_start": "início da coleta no meio do minuto",
}
BACKFILL_PREFIX = "derived/ops/backfill_files"

STATUS_KEY = "status.json"
HISTORY_KEY = "history.json"
PAGE_KEY = "index.html"
JSON_TYPE = "application/json"


class Sink(Protocol):
    def put(self, key: str, body: bytes, content_type: str, cache_control: str) -> None: ...


class S3Sink:
    """Bucket da página (produção). O CloudFront respeita o Cache-Control de cada objeto."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, body: bytes, content_type: str, cache_control: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=cache_control,
        )


class DirectorySink:
    """Diretório local (desenvolvimento): abra o index.html com um servidor HTTP simples."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, key: str, body: bytes, content_type: str, cache_control: str) -> None:
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, target)


@dataclass
class _Sample:
    ts_us: int
    channels: dict[tuple[str, int], dict[str, int]]
    """(instância, início da instância) → mensagens processadas por stream."""
    received: int
    rates: dict[str, float] | None = None
    total_rate: float | None = None


@dataclass
class StatusPublisher:
    """Grava status.json a cada `status_interval_s` e history.json quando muda (doc 06 §8.5)."""

    config: PipelineConfig
    sink: Sink
    data_s3: Any | None = None
    """Cliente S3 do bucket de dados: tamanho do bucket e contagens do backfill."""
    _samples: deque[_Sample] = field(init=False)
    _page_published: bool = field(default=False, init=False)
    _history_digest: str | None = field(default=None, init=False)
    _history_checked_us: int | None = field(default=None, init=False)
    _totals: dict[str, Any] = field(default_factory=dict, init=False)
    _totals_checked_us: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._samples = deque(maxlen=int(SERIES_WINDOW_S / self.config.status_interval_s) + 1)

    @property
    def cache_dir(self) -> Path:
        return self.config.data_dir / "public-status"

    def run(self, stop: threading.Event, now_us: Callable[[], int] | None = None) -> None:
        clock = now_us or (lambda: time.time_ns() // 1000)
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.tick(clock())
            except Exception:
                # A página é acessória: uma falha (S3, arquivo em escrita) espera o próximo ciclo.
                log.exception("falha ao publicar o status")
            stop.wait(max(0.0, self.config.status_interval_s - (time.monotonic() - started)))

    def tick(self, now_us: int) -> None:
        if not self._page_published:
            page = files("trader.public").joinpath(PAGE_KEY).read_bytes()
            self.sink.put(PAGE_KEY, page, "text/html; charset=utf-8", "public, max-age=60")
            self._page_published = True
        if _due(self._totals_checked_us, now_us, TOTALS_CHECK_S):
            self._refresh_totals()
            self._totals_checked_us = now_us
        if _due(self._history_checked_us, now_us, HISTORY_CHECK_S):
            self._publish_history(now_us)
            self._history_checked_us = now_us
        body = _json(self.status(now_us))
        self.sink.put(STATUS_KEY, body, JSON_TYPE, "public, max-age=10")

    # ------------------------------------------------------------------ status.json

    def status(self, now_us: int) -> dict[str, Any]:
        healths = [h for p in health_files(self.config.data_dir) if (h := read_health(p))]
        running = [
            h
            for h in healths
            if h.get("status") == "running" and now_us - int(h["updated_ts_us"]) <= STALE_HEALTH_US
        ]
        with _state(self.config.data_dir) as db:
            sessions = _sessions(db)
            gaps = _gaps(db, now_us)
            storage = _storage(db)
        open_messages = sum(sum(h.get("session_messages", {}).values()) for h in running)
        sample = self._sample(now_us, running, sessions["closed_messages"] + open_messages)
        streams = self.config.streams
        channels = []
        for stream in streams:
            suffix = stream.partition("@")[2]
            # Silêncio do canal no instante em que o coletor gravou a saúde (a cada 5 s).
            ages = [
                max(0, int(h["updated_ts_us"]) - int(h["channels"][stream]["last_recv_ts_us"]))
                for h in running
                if h["channels"].get(stream, {}).get("last_recv_ts_us")
            ]
            channels.append(
                {
                    "id": suffix,
                    "name": CHANNEL_NAMES.get(suffix, suffix),
                    "age_s": round(min(ages) / SECOND_US, 1) if ages else None,
                    "rate": _round(sample.rates.get(suffix) if sample.rates else None, 2),
                }
            )
        newest = max(running, key=lambda h: int(h["updated_ts_us"]), default=None)
        if running:
            state = "running"
        elif healths:
            state = "stale" if any(h.get("status") == "running" for h in healths) else "stopped"
        else:
            state = "unknown"
        offset = newest.get("clock_offset_us") if newest else None
        return {
            "schema": SCHEMA_VERSION,
            "generated_at_us": now_us,
            "interval_s": self.config.status_interval_s,
            "collector": {
                "state": state,
                "since_us": min(int(h["started_ts_us"]) for h in running) if running else None,
                "version": newest.get("version") if newest else None,
            },
            "collection_start_us": sessions["first_opened_us"],
            "channels": channels,
            "messages": {
                "received": sample.received,
                "rate": _round(sample.total_rate, 1),
            },
            "sessions": {
                "total": sessions["total"],
                "reconnects": sessions["reconnects"],
            },
            "clock_offset_ms": None if offset is None else round(offset / 1000, 1),
            "gaps": gaps,
            "storage": {**storage, **self._totals.get("bucket", {})},
            "history": self._totals.get("history", {}),
            "series": [
                {"t": s.ts_us, "total": _round(s.total_rate, 1), "rates": s.rates}
                for s in self._samples
                if s.rates is not None
            ],
        }

    def _sample(self, now_us: int, running: list[dict[str, Any]], received: int) -> _Sample:
        channels = {
            (str(h["instance"]), int(h["started_ts_us"])): {
                stream: int(info["messages"]) for stream, info in h["channels"].items()
            }
            for h in running
        }
        sample = _Sample(now_us, channels, received)
        previous = self._samples[-1] if self._samples else None
        if previous is not None and now_us > previous.ts_us:
            seconds = (now_us - previous.ts_us) / SECOND_US
            common = channels.keys() & previous.channels.keys()
            # Na troca a/b as duas instâncias processam ao mesmo tempo: vale a maior taxa.
            sample.rates = {
                stream.partition("@")[2]: round(
                    max(
                        (
                            max(0, channels[k].get(stream, 0) - previous.channels[k].get(stream, 0))
                            for k in common
                        ),
                        default=0,
                    )
                    / seconds,
                    2,
                )
                for stream in self.config.streams
            }
            sample.total_rate = max(0, received - previous.received) / seconds
        self._samples.append(sample)
        return sample

    # ------------------------------------------------------------------ history.json

    def _publish_history(self, now_us: int) -> None:
        """Só grava quando muda: em geral uma vez por hora, depois do pipeline."""
        document = self.history(now_us)
        digest = hashlib.sha256(_json(document)).hexdigest()
        if digest == self._history_digest:
            return
        body = _json({**document, "generated_at_us": now_us})
        self.sink.put(HISTORY_KEY, body, JSON_TYPE, "public, max-age=300")
        self._history_digest = digest

    def history(self, now_us: int) -> dict[str, Any]:
        """Por hora dos últimos 7 dias: linhas normalizadas, minutos completos e atraso."""
        last = now_us // HOUR_US
        first = last - HISTORY_HOURS
        hours: dict[int, dict[str, Any]] = {}
        with _state(self.config.data_dir) as db:
            if db is not None:
                rows = db.execute(
                    "SELECT hour, key, value FROM hour_stats"
                    " WHERE symbol = ? AND metric = 'rows' AND hour >= ? AND hour < ?",
                    (self.config.symbol, first, last),
                ).fetchall()
                for hour, key, value in rows:
                    hours.setdefault(hour, {}).setdefault("rows", {})[key] = int(value)
        start = collection_start(self.config.data_dir / "state.db")
        first_minute = None if start is None else -(-start // MINUTE_US) * MINUTE_US
        cache_path = self.cache_dir / "history-cache.json"
        try:
            cache: dict[str, dict[str, Any]] = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            cache = {}
        seen: dict[str, dict[str, Any]] = {}
        summaries: tuple[tuple[str, str, Callable[[Path], Any]], ...] = (
            ("derived/quality_mask", "minutes", lambda path: _minutes(path, first_minute or 0)),
            ("normalized/trades", "delay_ms", _delays),
        )
        for dataset, field_name, summarize in summaries:
            for path in hourly_files(self.config.data_dir, dataset, self.config.symbol):
                hour = hour_of(path) // HOUR_US
                if not first <= hour < last:
                    continue
                key = str(path.relative_to(self.config.data_dir))
                stat = path.stat()
                signature = f"{stat.st_mtime_ns}:{stat.st_size}:{first_minute}"
                entry = cache.get(key)
                if entry is None or entry["sig"] != signature:
                    entry = {"sig": signature, "value": summarize(path)}
                seen[key] = entry
                if entry["value"] is not None:
                    hours.setdefault(hour, {})[field_name] = entry["value"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(seen, sort_keys=True))
        return {
            "schema": SCHEMA_VERSION,
            "collection_start_us": start,
            "hours": [{"h": hour * 3600, **hours[hour]} for hour in sorted(hours)],
        }

    # ------------------------------------------------------------------ totais (a cada 6 h)

    def _refresh_totals(self) -> None:
        bucket = self.config.replica_s3_bucket
        history_dir = self.config.data_dir
        if self.data_s3 is not None and bucket:
            objects, size = 0, 0
            history_dir = self.cache_dir
            paginator = self.data_s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=""):
                for obj in page.get("Contents", []):
                    objects += 1
                    size += int(obj.get("Size", 0))
                    if obj["Key"].startswith(BACKFILL_PREFIX) and obj["Key"].endswith(".parquet"):
                        target = history_dir / obj["Key"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.data_s3.download_file(bucket, obj["Key"], str(target))
            self._totals["bucket"] = {"bucket_objects": objects, "bucket_bytes": size}
        self._totals["history"] = {
            dataset: {"files": files_, "rows": rows, "first_day": first, "last_day": last}
            for dataset, (files_, rows, first, last) in history_counts(history_dir).items()
        }


def _due(checked_us: int | None, now_us: int, every_s: float) -> bool:
    return checked_us is None or now_us - checked_us >= every_s * SECOND_US


@contextmanager
def _state(data_dir: Path) -> Iterator[sqlite3.Connection | None]:
    """state.db só para leitura: o coletor e o pipeline continuam donos da escrita."""
    path = data_dir / "state.db"
    if not path.exists():
        yield None
        return
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        yield db
    finally:
        db.close()


def _sessions(db: sqlite3.Connection | None) -> dict[str, Any]:
    if db is None:
        return {"total": 0, "reconnects": 0, "closed_messages": 0, "first_opened_us": None}
    total, first, closed_messages = db.execute(
        "SELECT count(*), min(opened_ts_us),"
        " coalesce(sum(CASE WHEN closed_ts_us IS NOT NULL THEN messages END), 0) FROM sessions"
    ).fetchone()
    placeholders = ",".join("?" * len(PLANNED_CLOSES))
    (reconnects,) = db.execute(
        "SELECT count(*) FROM sessions WHERE closed_ts_us IS NOT NULL"
        f" AND close_reason NOT IN ({placeholders})",
        PLANNED_CLOSES,
    ).fetchone()
    return {
        "total": int(total),
        "reconnects": int(reconnects),
        "closed_messages": int(closed_messages),
        "first_opened_us": None if first is None else int(first),
    }


def _gaps(db: sqlite3.Connection | None, now_us: int) -> dict[str, Any]:
    if db is None:
        return {"by_status": {}, "recent": []}
    by_status = dict(db.execute("SELECT status, count(*) FROM gaps GROUP BY status").fetchall())
    rows = db.execute(
        "SELECT channel, cause, status, start_ts_us, end_ts_us FROM gaps"
        " WHERE detected_ts_us >= ? ORDER BY start_ts_us DESC LIMIT ?",
        (now_us - GAPS_WINDOW_US, GAPS_LISTED),
    ).fetchall()
    recent = [
        {
            "channel": CHANNEL_NAMES.get(channel.partition("@")[2], channel),
            "cause": CAUSES.get(cause, cause),
            "status": status,
            "start_us": int(start),
            "duration_s": round((end - start) / SECOND_US, 1),
        }
        for channel, cause, status, start, end in rows
    ]
    return {"by_status": by_status, "recent": recent}


def _storage(db: sqlite3.Connection | None) -> dict[str, Any]:
    if db is None:
        return {}
    files_, received, stored = db.execute(
        "SELECT count(*), coalesce(sum(original_size), 0), coalesce(sum(compressed_size), 0)"
        " FROM raw_files"
    ).fetchone()
    return {
        "raw_files": int(files_),
        "raw_received_bytes": int(received),
        "raw_stored_bytes": int(stored),
    }


def _minutes(path: Path, first_minute_us: int) -> dict[str, list[int]] | None:
    """Por canal: minutos [ok, recuperados, ausentes, outros] da máscara de qualidade.

    Minutos anteriores ao início da coleta ficam de fora: não são perda."""
    table = pq.read_table(path, columns=["channel", "minute_ts_us", "status"])
    table = table.filter(pc.greater_equal(table["minute_ts_us"], first_minute_us))
    result: dict[str, list[int]] = {}
    for channel, status in zip(
        table["channel"].to_pylist(), table["status"].to_pylist(), strict=True
    ):
        counts = result.setdefault(channel.partition("@")[2], [0, 0, 0, 0])
        index = {"ok": 0, "recovered": 1, "missing": 2}.get(status, 3)
        counts[index] += 1
    return result or None


def _delays(path: Path) -> list[float] | None:
    """[p50, p99] em ms do negócio na Binance ao recebimento, só dos negócios ao vivo."""
    table = pq.read_table(path, columns=["trade_ts_us", "recv_ts_us"])
    table = table.filter(pc.is_valid(table["recv_ts_us"]))
    if table.num_rows == 0:
        return None
    delay_ms = pc.divide(
        pc.cast(pc.subtract(table["recv_ts_us"], table["trade_ts_us"]), pa.float64()), 1000.0
    )
    return [round(q, 1) for q in pc.quantile(delay_ms, q=[0.5, 0.99]).to_pylist()]


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _json(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
