"""Gravador da camada bruta (doc 06 §5.2; RF-131, RF-135).

Um arquivo por canal, sessão e hora UTC:
``raw/binance-spot/<ativo>/<canal>/date=AAAA-MM-DD/hour=HH/<sessão>.jsonl.open``.
Ao fechar (virada da hora ou fim da sessão), o arquivo recebe fsync e é renomeado para
``.jsonl``; o pipeline só lê arquivos fechados (doc 03 §4.2).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from trader.core.rawformat import RawRecord, encode
from trader.storage.layout import (
    HOUR_US,
    OPEN_SUFFIX,
    StorageError,
    channel_dir,
    fsync_dir,
    hour_dirs,
    raw_root,
)

__all__ = ["HOUR_US", "RawWriter", "StorageError"]


@dataclass(slots=True)
class _OpenFile:
    path: Path
    handle: BinaryIO
    hour: int


class RawWriter:
    def __init__(self, data_dir: Path, symbol: str) -> None:
        self._root = raw_root(data_dir, symbol)
        self._files: dict[tuple[str, str], _OpenFile] = {}
        # A hora de cada arquivo nunca retrocede, mesmo que o relógio volte (evita reabrir
        # e sobrescrever um arquivo já fechado).
        self._last_hour: dict[tuple[str, str], int] = {}

    def write(self, record: RawRecord) -> None:
        key = (record.channel, record.session)
        hour = max(record.recv_ts_us // HOUR_US, self._last_hour.get(key, 0))
        current = self._files.get(key)
        if current is not None and hour > current.hour:
            self._seal(key)
            current = None
        if current is None:
            current = self._open(key, hour)
        line = encode(record)
        try:
            current.handle.write(line)
        except OSError as exc:
            raise StorageError(f"falha ao gravar {current.path}: {exc}") from exc

    def flush(self) -> None:
        """Grava em disco tudo o que foi recebido até agora (chamado a cada 1 s)."""
        for open_file in self._files.values():
            try:
                open_file.handle.flush()
                os.fsync(open_file.handle.fileno())
            except OSError as exc:
                raise StorageError(f"falha no fsync de {open_file.path}: {exc}") from exc

    def seal_stale(self, now_us: int) -> None:
        """Fecha arquivos de horas já encerradas, mesmo sem mensagens novas no canal."""
        hour = now_us // HOUR_US
        for key in [k for k, f in self._files.items() if f.hour < hour]:
            self._seal(key)

    def seal_session(self, session: str) -> None:
        for key in [k for k in self._files if k[1] == session]:
            self._seal(key)

    def close(self) -> None:
        for key in list(self._files):
            self._seal(key)

    @property
    def open_paths(self) -> list[Path]:
        return [f.path for f in self._files.values()]

    def _open(self, key: tuple[str, str], hour: int) -> _OpenFile:
        channel, session = key
        directory = self._root / channel_dir(channel)
        for part in hour_dirs(hour):
            directory /= part
        path = directory / f"{session}.jsonl{OPEN_SUFFIX}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle = path.open("ab")
        except OSError as exc:
            raise StorageError(f"falha ao abrir {path}: {exc}") from exc
        open_file = _OpenFile(path, handle, hour)
        self._files[key] = open_file
        self._last_hour[key] = hour
        return open_file

    def _seal(self, key: tuple[str, str]) -> None:
        open_file = self._files.pop(key)
        final = open_file.path.with_name(open_file.path.name.removesuffix(OPEN_SUFFIX))
        try:
            open_file.handle.flush()
            os.fsync(open_file.handle.fileno())
            open_file.handle.close()
            os.rename(open_file.path, final)
            fsync_dir(final.parent)
        except OSError as exc:
            raise StorageError(f"falha ao fechar {open_file.path}: {exc}") from exc
