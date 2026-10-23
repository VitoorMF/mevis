"""Arquivo de quarentena (doc 06 §5.1): registros recusados, uma linha JSON por registro."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import zstandard


class QuarantineWriter:
    """Linhas JSON comprimidas com zstd; o arquivo só aparece se houver ao menos uma linha."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        self._tmp = path.with_name(path.name + ".tmp")
        self._handle: Any = None
        self._writer: Any = None

    def add(self, entry: Mapping[str, Any]) -> None:
        if self._writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._tmp.open("wb")
            self._writer = zstandard.ZstdCompressor(level=9).stream_writer(self._handle)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._writer.write(line.encode() + b"\n")
        self.count += 1

    def close(self) -> bool:
        if self._writer is None:
            self.path.unlink(missing_ok=True)
            return False
        self._writer.close()
        self._tmp.replace(self.path)
        return True

    def abort(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._tmp.unlink(missing_ok=True)
