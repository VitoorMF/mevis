"""Cópia local de objetos do S3 que só baixa o que mudou, pelo ETag (doc 06 §8.4; doc 03 §5.1).

Usada pelo `dashboard-data` e pela montagem da série de minutos das features. O arquivo
`.s3-etags.json` na raiz do cache guarda o ETag de cada objeto baixado.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MANIFEST = ".s3-etags.json"


def list_objects(client: Any, bucket: str, prefix: str) -> Iterator[dict[str, Any]]:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        yield from page.get("Contents", [])


class S3Cache:
    def __init__(self, client: Any, bucket: str, root: Path) -> None:
        self.client = client
        self.bucket = bucket
        self.root = root
        self.downloaded = 0
        self.unchanged = 0
        try:
            self._etags: dict[str, str] = json.loads((root / MANIFEST).read_text())
        except (OSError, ValueError):
            self._etags = {}

    def list(self, prefix: str) -> list[dict[str, Any]]:
        return list(list_objects(self.client, self.bucket, prefix))

    def get(self, key: str, listed: dict[str, Any] | None = None) -> Path:
        """Caminho local de `key`, baixado só se o ETag mudou desde a última vez."""
        if listed is None:
            listed = {"ETag": self.client.head_object(Bucket=self.bucket, Key=key)["ETag"]}
        target = self.root / key
        if self._etags.get(key) == listed["ETag"] and target.exists():
            self.unchanged += 1
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        self.client.download_file(self.bucket, key, str(tmp))
        os.replace(tmp, target)
        self._etags[key] = listed["ETag"]
        self.downloaded += 1
        return target

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / MANIFEST).write_text(json.dumps(self._etags, indent=1, sort_keys=True))
