"""Armazenamento durável fora do host (RF-136). O S3 entra junto com a infraestrutura AWS."""

import shutil
from pathlib import Path
from typing import Protocol

from trader.storage.layout import StorageError, replace_atomically
from trader.storage.rawfiles import file_sha256


class ObjectStore(Protocol):
    def put(self, local: Path, key: str, sha256: str) -> None:
        """Copia `local` para `key` e confirma a integridade pelo SHA-256 dos bytes."""
        ...


class LocalDirectoryStore:
    """Réplica num diretório local: desenvolvimento e testes."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, local: Path, key: str, sha256: str) -> None:
        target = self._root / key
        tmp = target.with_name(target.name + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, tmp)
            if file_sha256(tmp) != sha256:
                tmp.unlink()
                raise StorageError(f"cópia corrompida: {key}")
            replace_atomically(tmp, target)
        except OSError as exc:
            raise StorageError(f"falha ao replicar {key}: {exc}") from exc
