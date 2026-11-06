"""Armazenamento durável fora do host (RF-136): S3 em produção, diretório local nos testes."""

import base64
import hashlib
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

    def get(self, key: str, local: Path, sha256: str) -> None:
        """Traz `key` para `local`, conferindo o SHA-256 dos bytes antes de colocá-lo no lugar."""
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

    def get(self, key: str, local: Path, sha256: str) -> None:
        tmp = local.with_name(local.name + ".tmp")
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._root / key, tmp)
        except OSError as exc:
            raise StorageError(f"falha ao baixar {key}: {exc}") from exc
        _install(tmp, local, key, sha256)

    def list_keys(self, prefix: str) -> list[str]:
        """Chaves que começam com `prefix`, em ordem."""
        base = self._root / prefix
        start = base if base.is_dir() else base.parent
        if not start.is_dir():
            return []
        keys = (p.relative_to(self._root).as_posix() for p in start.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix) and not k.endswith(".tmp"))

    def fetch(self, key: str, local: Path) -> str:
        """Traz `key` para `local` sem SHA-256 conhecido; devolve o dos bytes copiados."""
        tmp = local.with_name(local.name + ".tmp")
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._root / key, tmp)
        except OSError as exc:
            raise StorageError(f"falha ao baixar {key}: {exc}") from exc
        sha256 = file_sha256(tmp)
        replace_atomically(tmp, local)
        return sha256


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

    def get(self, key: str, local: Path, sha256: str) -> None:
        tmp = local.with_name(local.name + ".tmp")
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(self._bucket, self._prefix + key, str(tmp))
        except (OSError, BotoCoreError, ClientError) as exc:
            raise StorageError(f"falha ao baixar {key} do S3: {exc}") from exc
        _install(tmp, local, key, sha256)

    def list_keys(self, prefix: str) -> list[str]:
        """Chaves que começam com `prefix`, em ordem."""
        keys: list[str] = []
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=self._prefix + prefix
            )
            for page in pages:
                keys += [o["Key"][len(self._prefix) :] for o in page.get("Contents", [])]
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"falha ao listar {prefix} no S3: {exc}") from exc
        return sorted(keys)

    def fetch(self, key: str, local: Path) -> str:
        """Traz `key` para `local` e devolve o SHA-256 dos bytes, conferido com o que o S3 guardou
        no envio (`put` sempre manda o SHA-256)."""
        tmp = local.with_name(local.name + ".tmp")
        digest = hashlib.sha256()
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            response = self._client.get_object(
                Bucket=self._bucket, Key=self._prefix + key, ChecksumMode="ENABLED"
            )
            with tmp.open("wb") as out:
                for chunk in response["Body"].iter_chunks(1 << 20):
                    digest.update(chunk)
                    out.write(chunk)
        except (OSError, BotoCoreError, ClientError) as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"falha ao baixar {key} do S3: {exc}") from exc
        sha256 = digest.hexdigest()
        stored = response.get("ChecksumSHA256")
        full_object = response.get("ChecksumType", "FULL_OBJECT") == "FULL_OBJECT"
        if stored and full_object and base64.b64decode(stored).hex() != sha256:
            tmp.unlink()
            raise StorageError(f"cópia corrompida: {key}")
        replace_atomically(tmp, local)
        return sha256


def _install(tmp: Path, local: Path, key: str, sha256: str) -> None:
    if file_sha256(tmp) != sha256:
        tmp.unlink()
        raise StorageError(f"cópia corrompida: {key}")
    replace_atomically(tmp, local)
