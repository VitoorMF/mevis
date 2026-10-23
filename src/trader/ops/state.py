"""Estado operacional em SQLite (ADR-02): sessões, lacunas, offsets de relógio e marcas d'água.

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
"""


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
        rows = self._db.execute(
            "SELECT gap_id, channel, start_ts_us, end_ts_us, cause, detected_ts_us, recoverable,"
            " first_missing_id, last_missing_id, status FROM gaps"
            " WHERE detected_ts_us >= ? ORDER BY gap_id",
            (detected_from_us,),
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
