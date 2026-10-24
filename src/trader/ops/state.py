"""Estado operacional em SQLite (ADR-02): sessões, lacunas, offsets de relógio, marcas d'água
e arquivos importados pelo backfill.

Exportado para os datasets operacionais pelo pipeline (doc 06 §5.4).
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    endpoint       TEXT NOT NULL,
    channels       TEXT NOT NULL,
    opened_ts_us   INTEGER NOT NULL,
    closed_ts_us   INTEGER,
    close_reason   TEXT,
    messages       INTEGER NOT NULL DEFAULT 0,
    code_version   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
    gap_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel           TEXT NOT NULL,
    start_ts_us       INTEGER NOT NULL,
    end_ts_us         INTEGER NOT NULL,
    cause             TEXT NOT NULL,
    detected_ts_us    INTEGER NOT NULL,
    recoverable       INTEGER NOT NULL,
    status            TEXT NOT NULL,
    first_missing_id  INTEGER,
    last_missing_id   INTEGER,
    recovery_origin   TEXT
);
CREATE INDEX IF NOT EXISTS gaps_by_detection ON gaps (detected_ts_us);
CREATE TABLE IF NOT EXISTS clock_offsets (
    local_ts_us   INTEGER PRIMARY KEY,
    server_ts_us  INTEGER NOT NULL,
    rtt_us        INTEGER NOT NULL,
    offset_us     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS watermarks (
    name      TEXT PRIMARY KEY,
    last_id   INTEGER,
    last_ts_us INTEGER
);
CREATE TABLE IF NOT EXISTS raw_files (
    rel_path          TEXT PRIMARY KEY,
    channel_dir       TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    hour              INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    file_sha256       TEXT NOT NULL,
    original_size     INTEGER NOT NULL,
    compressed_size   INTEGER NOT NULL,
    compressed_ts_us  INTEGER NOT NULL,
    replicated_ts_us  INTEGER,
    deleted_ts_us     INTEGER
);
CREATE INDEX IF NOT EXISTS raw_files_by_hour ON raw_files (hour);
CREATE TABLE IF NOT EXISTS outputs (
    rel_path          TEXT PRIMARY KEY,
    hour              INTEGER,
    file_sha256       TEXT NOT NULL,
    written_ts_us     INTEGER NOT NULL,
    replicated_ts_us  INTEGER,
    deleted_ts_us     INTEGER
);
CREATE TABLE IF NOT EXISTS normalized_hours (
    symbol            TEXT NOT NULL,
    hour              INTEGER NOT NULL,
    inputs_digest     TEXT NOT NULL,
    normalized_ts_us  INTEGER NOT NULL,
    PRIMARY KEY (symbol, hour)
);
CREATE TABLE IF NOT EXISTS hour_stats (
    symbol            TEXT NOT NULL,
    hour              INTEGER NOT NULL,
    metric            TEXT NOT NULL,
    key               TEXT NOT NULL,
    value             INTEGER NOT NULL,
    PRIMARY KEY (symbol, hour, metric, key)
);
CREATE TABLE IF NOT EXISTS backfill_files (
    file_name         TEXT PRIMARY KEY,
    dataset           TEXT NOT NULL,
    first_day         TEXT NOT NULL,
    last_day          TEXT NOT NULL,
    status            TEXT NOT NULL,
    sha256            TEXT,
    rows              INTEGER NOT NULL,
    quarantined       INTEGER NOT NULL,
    duplicates        INTEGER NOT NULL,
    missing_ids       INTEGER NOT NULL,
    missing_minutes   INTEGER NOT NULL,
    outputs           TEXT NOT NULL,
    finished_ts_us    INTEGER NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class RawFileRow:
    """Arquivo bruto comprimido, registrado pelo pipeline (doc 03 §6.3)."""

    rel_path: str
    channel_dir: str
    session_id: str
    hour: int
    sha256: str
    """Conteúdo original (descomprimido)."""
    file_sha256: str
    """Bytes do `.zst`, para conferir a réplica."""
    original_size: int
    compressed_size: int
    compressed_ts_us: int
    replicated_ts_us: int | None = None
    deleted_ts_us: int | None = None


@dataclass(frozen=True, slots=True)
class OutputRow:
    """Arquivo produzido pelo pipeline (Parquet, quarentena, exportações)."""

    rel_path: str
    hour: int | None
    file_sha256: str
    written_ts_us: int
    replicated_ts_us: int | None = None
    deleted_ts_us: int | None = None


@dataclass(frozen=True, slots=True)
class BackfillFileRow:
    """Resultado da importação de um arquivo público (doc 06 §7)."""

    file_name: str
    dataset: str
    first_day: str
    last_day: str
    status: str
    """`imported`, `rejected` (checksum) ou `missing` (ainda não publicado)."""
    sha256: str | None
    rows: int
    quarantined: int
    duplicates: int
    missing_ids: int
    missing_minutes: int
    outputs: tuple[str, ...]
    finished_ts_us: int


@dataclass(frozen=True, slots=True)
class GapRecord:
    channel: str
    start_ts_us: int
    end_ts_us: int
    cause: str
    detected_ts_us: int
    recoverable: bool
    first_missing_id: int | None = None
    last_missing_id: int | None = None

    @property
    def initial_status(self) -> str:
        return "open" if self.recoverable else "irrecoverable"


@dataclass(frozen=True, slots=True)
class StoredGap:
    gap_id: int
    record: GapRecord
    status: str


@dataclass(frozen=True, slots=True)
class SessionRow:
    session_id: str
    endpoint: str
    opened_ts_us: int
    closed_ts_us: int | None
    close_reason: str | None
    messages: int


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def session_opened(
        self,
        session_id: str,
        endpoint: str,
        channels: Sequence[str],
        opened_ts_us: int,
        code_version: str,
    ) -> None:
        self._db.execute(
            "INSERT INTO sessions (session_id, endpoint, channels, opened_ts_us, code_version)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, endpoint, json.dumps(list(channels)), opened_ts_us, code_version),
        )

    def session_closed(
        self, session_id: str, closed_ts_us: int, reason: str, messages: int
    ) -> None:
        self._db.execute(
            "UPDATE sessions SET closed_ts_us = ?, close_reason = ?, messages = ?"
            " WHERE session_id = ?",
            (closed_ts_us, reason, messages, session_id),
        )

    def sessions(self) -> list[SessionRow]:
        rows = self._db.execute(
            "SELECT session_id, endpoint, opened_ts_us, closed_ts_us, close_reason, messages"
            " FROM sessions ORDER BY opened_ts_us"
        )
        return [SessionRow(*row) for row in rows]

    def record_gap(self, gap: GapRecord) -> int:
        cursor = self._db.execute(
            "INSERT INTO gaps (channel, start_ts_us, end_ts_us, cause, detected_ts_us,"
            " recoverable, status, first_missing_id, last_missing_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gap.channel,
                gap.start_ts_us,
                gap.end_ts_us,
                gap.cause,
                gap.detected_ts_us,
                int(gap.recoverable),
                gap.initial_status,
                gap.first_missing_id,
                gap.last_missing_id,
            ),
        )
        return int(cursor.lastrowid or 0)

    def gaps_since(self, detected_from_us: int) -> list[StoredGap]:
        return self._gaps("detected_ts_us >= ?", (detected_from_us,))

    def record_clock_offset(
        self, local_ts_us: int, server_ts_us: int, rtt_us: int, offset_us: int
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO clock_offsets VALUES (?, ?, ?, ?)",
            (local_ts_us, server_ts_us, rtt_us, offset_us),
        )

    def save_watermarks(self, marks: dict[str, tuple[int | None, int | None]]) -> None:
        with self._db:
            self._db.execute("BEGIN")
            self._db.executemany(
                "INSERT OR REPLACE INTO watermarks VALUES (?, ?, ?)",
                [(name, last_id, last_ts) for name, (last_id, last_ts) in marks.items()],
            )

    def load_watermarks(self) -> dict[str, tuple[int | None, int | None]]:
        rows = self._db.execute("SELECT name, last_id, last_ts_us FROM watermarks")
        return {name: (last_id, last_ts) for name, last_id, last_ts in rows}

    # ------------------------------------------------------------------ pipeline

    def closed_sessions(self) -> set[str]:
        rows = self._db.execute("SELECT session_id FROM sessions WHERE closed_ts_us IS NOT NULL")
        return {row[0] for row in rows}

    def gaps_overlapping(self, start_ts_us: int, end_ts_us: int) -> list[StoredGap]:
        """Lacunas cujo intervalo cruza `[start_ts_us, end_ts_us)`."""
        return self._gaps("start_ts_us < ? AND end_ts_us > ?", (end_ts_us, start_ts_us))

    def clock_offsets_between(self, start_ts_us: int, end_ts_us: int) -> list[tuple[int, int]]:
        rows = self._db.execute(
            "SELECT local_ts_us, offset_us FROM clock_offsets"
            " WHERE local_ts_us >= ? AND local_ts_us < ? ORDER BY local_ts_us",
            (start_ts_us, end_ts_us),
        )
        return [(row[0], row[1]) for row in rows]

    def register_raw_file(self, row: RawFileRow) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO raw_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.rel_path,
                row.channel_dir,
                row.session_id,
                row.hour,
                row.sha256,
                row.file_sha256,
                row.original_size,
                row.compressed_size,
                row.compressed_ts_us,
                row.replicated_ts_us,
                row.deleted_ts_us,
            ),
        )

    def raw_files(self, where: str = "1", params: Sequence[object] = ()) -> list[RawFileRow]:
        rows = self._db.execute(
            f"SELECT * FROM raw_files WHERE {where} ORDER BY rel_path",
            tuple(params),
        )
        return [RawFileRow(*row) for row in rows]

    def raw_file(self, rel_path: str) -> RawFileRow | None:
        found = self.raw_files("rel_path = ?", (rel_path,))
        return found[0] if found else None

    def mark_raw(
        self,
        rel_path: str,
        *,
        replicated_ts_us: int | None = None,
        deleted_ts_us: int | None = None,
    ) -> None:
        if replicated_ts_us is not None:
            self._db.execute(
                "UPDATE raw_files SET replicated_ts_us = ? WHERE rel_path = ?",
                (replicated_ts_us, rel_path),
            )
        if deleted_ts_us is not None:
            self._db.execute(
                "UPDATE raw_files SET deleted_ts_us = ? WHERE rel_path = ?",
                (deleted_ts_us, rel_path),
            )

    def register_output(
        self, rel_path: str, hour: int | None, file_sha256: str, ts_us: int
    ) -> None:
        """Registra um arquivo produzido. Conteúdo novo precisa ser replicado de novo."""
        current = self.outputs("rel_path = ?", (rel_path,))
        if current and current[0].file_sha256 == file_sha256 and current[0].deleted_ts_us is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO outputs VALUES (?, ?, ?, ?, NULL, NULL)",
            (rel_path, hour, file_sha256, ts_us),
        )

    def outputs(self, where: str = "1", params: Sequence[object] = ()) -> list[OutputRow]:
        rows = self._db.execute(
            f"SELECT * FROM outputs WHERE {where} ORDER BY rel_path",
            tuple(params),
        )
        return [OutputRow(*row) for row in rows]

    def mark_output(
        self,
        rel_path: str,
        *,
        replicated_ts_us: int | None = None,
        deleted_ts_us: int | None = None,
    ) -> None:
        if replicated_ts_us is not None:
            self._db.execute(
                "UPDATE outputs SET replicated_ts_us = ? WHERE rel_path = ?",
                (replicated_ts_us, rel_path),
            )
        if deleted_ts_us is not None:
            self._db.execute(
                "UPDATE outputs SET deleted_ts_us = ? WHERE rel_path = ?",
                (deleted_ts_us, rel_path),
            )

    def normalized_digest(self, symbol: str, hour: int) -> str | None:
        row = self._db.execute(
            "SELECT inputs_digest FROM normalized_hours WHERE symbol = ? AND hour = ?",
            (symbol, hour),
        ).fetchone()
        return None if row is None else str(row[0])

    def set_normalized(self, symbol: str, hour: int, digest: str, ts_us: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO normalized_hours VALUES (?, ?, ?, ?)",
            (symbol, hour, digest, ts_us),
        )

    def set_hour_stats(self, symbol: str, hour: int, stats: dict[tuple[str, str], int]) -> None:
        """Substitui as contagens de uma hora normalizada: (métrica, chave) → valor."""
        self._db.execute("BEGIN")
        try:
            self._db.execute("DELETE FROM hour_stats WHERE symbol = ? AND hour = ?", (symbol, hour))
            self._db.executemany(
                "INSERT INTO hour_stats VALUES (?, ?, ?, ?, ?)",
                [(symbol, hour, metric, key, value) for (metric, key), value in stats.items()],
            )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def hour_stats_between(
        self, symbol: str, start_hour: int, end_hour: int
    ) -> list[tuple[int, str, str, int]]:
        """(hora, métrica, chave, valor) das horas em `[start_hour, end_hour)`."""
        rows = self._db.execute(
            "SELECT hour, metric, key, value FROM hour_stats"
            " WHERE symbol = ? AND hour >= ? AND hour < ? ORDER BY hour",
            (symbol, start_hour, end_hour),
        )
        return [(row[0], row[1], row[2], row[3]) for row in rows]

    def clock_samples_between(self, start_ts_us: int, end_ts_us: int) -> list[tuple[int, int, int]]:
        """(local_ts_us, offset_us, rtt_us) das medições em `[start, end)`."""
        rows = self._db.execute(
            "SELECT local_ts_us, offset_us, rtt_us FROM clock_offsets"
            " WHERE local_ts_us >= ? AND local_ts_us < ? ORDER BY local_ts_us",
            (start_ts_us, end_ts_us),
        )
        return [(row[0], row[1], row[2]) for row in rows]

    def backfill_status(self, file_name: str) -> str | None:
        row = self._db.execute(
            "SELECT status FROM backfill_files WHERE file_name = ?", (file_name,)
        ).fetchone()
        return None if row is None else str(row[0])

    def record_backfill_file(self, row: BackfillFileRow) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO backfill_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.file_name,
                row.dataset,
                row.first_day,
                row.last_day,
                row.status,
                row.sha256,
                row.rows,
                row.quarantined,
                row.duplicates,
                row.missing_ids,
                row.missing_minutes,
                json.dumps(list(row.outputs)),
                row.finished_ts_us,
            ),
        )

    def table_rows(self, table: str) -> tuple[list[str], list[tuple[object, ...]]]:
        """Colunas e linhas de uma tabela operacional, para exportação (doc 06 §5.4)."""
        if table not in {"sessions", "gaps", "clock_offsets", "backfill_files", "hour_stats"}:
            raise ValueError(f"tabela não exportável: {table}")
        cursor = self._db.execute(f"SELECT * FROM {table}")
        return [d[0] for d in cursor.description], cursor.fetchall()

    def backup_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(path)
        try:
            self._db.backup(target)
        finally:
            target.close()

    def _gaps(self, where: str, params: Sequence[object]) -> list[StoredGap]:
        rows = self._db.execute(
            "SELECT gap_id, channel, start_ts_us, end_ts_us, cause, detected_ts_us, recoverable,"
            f" first_missing_id, last_missing_id, status FROM gaps WHERE {where}"
            " ORDER BY gap_id",
            tuple(params),
        )
        return [
            StoredGap(
                gap_id=row[0],
                record=GapRecord(
                    channel=row[1],
                    start_ts_us=row[2],
                    end_ts_us=row[3],
                    cause=row[4],
                    detected_ts_us=row[5],
                    recoverable=bool(row[6]),
                    first_missing_id=row[7],
                    last_missing_id=row[8],
                ),
                status=row[9],
            )
            for row in rows
        ]
