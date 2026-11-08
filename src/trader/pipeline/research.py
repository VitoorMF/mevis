"""Leitura dos datasets pela pesquisa: manifesto e Parquet pelo id, do Mac ou do S3 (doc 03 §5.3
e §5.4). Não usa `numpy`."""

import json
from pathlib import Path
from typing import Any, Protocol

from trader.storage.rawfiles import file_sha256

DATASETS = "derived/datasets"


class Fetcher(Protocol):
    def fetch(self, key: str, local: Path) -> str: ...


def read_manifest(dataset_id: str, datasets_dir: Path, fetcher: Fetcher) -> dict[str, Any]:
    """Manifesto do dataset, baixado do armazenamento se não estiver no Mac."""
    path = datasets_dir / f"{dataset_id}.manifest.json"
    if not path.exists():
        fetcher.fetch(f"{DATASETS}/{dataset_id}/manifest.json", path)
    manifest: dict[str, Any] = json.loads(path.read_text())
    if manifest.get("dataset_id") != dataset_id:
        raise ValueError(f"manifesto de outro dataset em {path}")
    return manifest


def dataset_file(
    dataset_id: str, manifest: dict[str, Any], datasets_dir: Path, fetcher: Fetcher
) -> Path:
    """Parquet do dataset, baixado se faltar e conferido pelo SHA-256 do manifesto."""
    path = datasets_dir / f"{dataset_id}.parquet"
    if not path.exists():
        fetcher.fetch(f"{DATASETS}/{dataset_id}/data.parquet", path)
    if file_sha256(path) != manifest["parquet_sha256"]:
        raise ValueError(f"{path}: SHA-256 diferente do manifesto")
    return path
