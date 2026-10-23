"""Armazenamento durável fora do host (RF-136): S3 em produção, diretório local nos testes."""

import base64
import shutil
from pathlib import Path
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

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


class S3ObjectStore:
    """Réplica no S3. O SHA-256 vai junto com o objeto; o S3 recusa o envio se não bater."""

    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def put(self, local: Path, key: str, sha256: str) -> None:
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        try:
            with local.open("rb") as body:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._prefix + key,
                    Body=body,
                    ChecksumSHA256=checksum,
                )
        except (OSError, BotoCoreError, ClientError) as exc:
            raise StorageError(f"falha ao enviar {key} ao S3: {exc}") from exc
